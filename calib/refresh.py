"""Camera calibration-refresh decision: given which corner markers a camera sees now and where its CURRENT accepted
pose says they should be, decide whether to keep the pose, re-solve, flag moved markers, or exclude the camera.

Pure logic: no OpenCV. The caller (calib/auto_pose.py in the worker) detects markers, projects the expected corners
with the accepted pose, optionally runs a solve, and calls decide_refresh().

Camera states (docs/CONTRACTS.md): TRUSTED (weight 1.0), DEGRADED (weight degraded_weight), EXCLUDED and OFFLINE
(weight 0: contribute nothing).

Rules (in order):
  A. Marker displaced: >= 2 usable markers visible, a strict majority unmoved, a minority moved -> the MARKER moved,
     not the camera. Keep pose + state, flag the moved IDs displaced (not used in solves; re-admitted when they line
     up with the accepted pose again, i.e. someone put them back).
  1. 4 visible -> solve from all 4; accept if reproj < reproj_thresh; replace the pose only if it changed by more
     than pose_eps_m / pose_eps_deg (no jitter). TRUSTED.
  2. 3 visible, all unmoved -> keep pose, TRUSTED.
  3. 3 visible, majority moved -> camera bumped: solve from the 3; reproj < thresh -> TRUSTED, else DEGRADED.
  4. 1-2 visible, all unmoved -> keep pose, TRUSTED.
  5. 1-2 visible, any moved (not rule A) -> EXCLUDED until the next retry.
  6. 0 visible -> EXCLUDED (routine).
Reconnect fast path (context="reconnect"): stored pose + >= 1 visible, all unmoved -> TRUSTED at once; stored pose +
none visible -> DEGRADED, re-check every verify_retry_s; markers moved -> normal rules; no stored pose -> EXCLUDED
until a 4-marker solve.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

TRUSTED, DEGRADED, EXCLUDED, OFFLINE = "TRUSTED", "DEGRADED", "EXCLUDED", "OFFLINE"

DEFAULTS = {"refresh_s": 60, "excluded_retry_s": 5, "verify_retry_s": 2, "pixel_tol": 8.0, "reproj_thresh": 4.5,
            "degraded_weight": 0.4, "stale_timeout": 2.5, "pose_eps_m": 0.01, "pose_eps_deg": 0.5,
            "reacquire_gate_max_m": 3.0}
CONFIG = Path(__file__).resolve().parent.parent / "config" / "refresh.json"


def load_thresholds(path=CONFIG, **overrides):
    """DEFAULTS <- config/refresh.json <- non-None overrides (CLI flags)."""
    th = dict(DEFAULTS)
    try:
        th.update({k: v for k, v in json.loads(Path(path).read_text()).items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    th.update({k: v for k, v in overrides.items() if v is not None and k in DEFAULTS})
    return th


def weight_for(state, th):
    return {TRUSTED: 1.0, DEGRADED: float(th["degraded_weight"])}.get(state, 0.0)


@dataclass
class Decision:
    action: str          # "keep" | "replace_pose" | "flag_displaced" | "exclude"
    state: str
    weight: float
    reasons: list
    branch: str = ""
    next_in_s: float = 60.0
    displaced: set = field(default_factory=set)   # updated displaced set (flags added / re-admitted)
    errors: dict = field(default_factory=dict)    # marker id -> mean corner error px vs the accepted pose
    moved: list = field(default_factory=list)


def _err(a, b):
    """Mean distance between two lists of 4 [x, y] corners."""
    return sum(((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5 for p, q in zip(a, b)) / len(a)


def _changed(solve, th):
    return solve.get("delta_m", 0.0) > th["pose_eps_m"] or solve.get("delta_deg", 0.0) > th["pose_eps_deg"]


def decide_refresh(visible, expected, displaced, solve_result, prev_state, context, thresholds):
    """
    visible:   {marker_id: 4 corners [[x, y], ...]} detected now (known corner markers only).
    expected:  {marker_id: 4 corners} projected with the CURRENT accepted pose, or None if there is no pose yet.
    displaced: set of marker ids currently flagged displaced.
    solve_result: None, or {"ids": [...], "err": reproj px, "delta_m": m, "delta_deg": deg} for a solve from the
                  visible non-displaced markers (the caller solves when >= 3 are usable; delta = vs accepted pose).
    prev_state: TRUSTED | DEGRADED | EXCLUDED | OFFLINE.   context: "routine" | "reconnect".
    """
    th = thresholds
    tol = th["pixel_tol"]
    reasons = []
    disp = set(displaced)
    errors = {}
    if expected is not None:
        errors = {i: round(_err(visible[i], expected[i]), 1) for i in visible if i in expected}
        for i in sorted(disp & set(errors)):  # displaced marker lined up again -> re-admit
            if errors[i] <= tol:
                disp.discard(i)
                reasons.append(f"marker {i} back in place ({errors[i]} px): re-admitted")
    usable = sorted(i for i in visible if i not in disp)
    moved = [i for i in usable if errors.get(i, 0.0) > tol]
    n, n_moved = len(usable), len(moved)
    solve_ok = solve_result is not None and solve_result.get("err", 1e9) < th["reproj_thresh"]

    def out(action, state, branch, next_in, why):
        reasons.append(why)
        nxt = {"refresh": th["refresh_s"], "excluded": th["excluded_retry_s"], "verify": th["verify_retry_s"]}[next_in]
        return Decision(action, state, weight_for(state, th), reasons, branch, nxt, disp, errors, moved)

    # ---- no accepted pose: only a good 4-marker solve can give one
    if expected is None:
        if solve_ok and len(solve_result.get("ids", [])) >= 4:
            return out("replace_pose", TRUSTED, "no-pose:solve4", "refresh",
                       f"first pose from a 4-marker solve ({solve_result['err']:.1f} px)")
        return out("exclude", EXCLUDED, "no-pose", "excluded", "no stored pose: waiting for a 4-marker solve")

    # ---- reconnect fast path
    if context == "reconnect":
        if n == 0:
            return out("keep", DEGRADED, "reconnect:no-markers", "verify",
                       "reconnected, no markers visible: using the stored pose unverified")
        if n_moved == 0:
            return out("keep", TRUSTED, "reconnect:verified", "refresh",
                       f"reconnected, markers {usable} where expected: stored pose verified")
        reasons.append("reconnected, markers moved: normal rules")

    # ---- A: a minority of markers moved -> the markers were kicked, not the camera
    if n >= 2 and 0 < n_moved and (n - n_moved) > n / 2:
        disp |= set(moved)
        state = prev_state if prev_state in (TRUSTED, DEGRADED) and context != "reconnect" else TRUSTED
        return out("flag_displaced", state, "A:marker-displaced", "refresh",
                   f"markers {moved} moved but {sorted(set(usable) - set(moved))} didn't: markers displaced, pose kept")

    if n >= 4:
        if solve_ok:
            if _changed(solve_result, th):
                return out("replace_pose", TRUSTED, "1:solve4", "refresh",
                           f"4-marker solve {solve_result['err']:.1f} px, pose changed "
                           f"{solve_result.get('delta_m', 0) * 100:.1f} cm / {solve_result.get('delta_deg', 0):.2f} deg")
            return out("keep", TRUSTED, "1:solve4-same", "refresh",
                       f"4-marker solve {solve_result['err']:.1f} px, change below eps: pose kept (no jitter)")
        if n_moved == 0:
            why = "no solve" if solve_result is None else f"solve rejected ({solve_result['err']:.1f} px)"
            return out("keep", TRUSTED, "1:unmoved", "refresh", f"4 markers where expected; {why}: pose kept")
        if solve_result is not None:
            return out("replace_pose", DEGRADED, "1:solve4-poor", "excluded",
                       f"camera moved, 4-marker solve only {solve_result['err']:.1f} px: accepted, DEGRADED")
        return out("exclude", EXCLUDED, "1:moved-no-solve", "excluded", "camera moved, no solve available")

    if n == 3:
        if n_moved == 0:
            return out("keep", TRUSTED, "2:three-unmoved", "refresh", "3 markers where expected: pose kept")
        if solve_result is None:
            return out("exclude", EXCLUDED, "3:moved-no-solve", "excluded", "camera bumped, no solve available")
        if solve_ok:
            return out("replace_pose", TRUSTED, "3:solve3", "refresh",
                       f"camera bumped: 3-marker solve {solve_result['err']:.1f} px")
        return out("replace_pose", DEGRADED, "3:solve3-poor", "excluded",
                   f"camera bumped: 3-marker solve only {solve_result['err']:.1f} px: accepted, DEGRADED")

    if n >= 1:
        if n_moved == 0:
            return out("keep", TRUSTED, "4:few-unmoved", "refresh", f"markers {usable} where expected: pose kept")
        return out("exclude", EXCLUDED, "5:few-moved", "excluded",
                   f"only {usable} visible and {moved} moved: can't tell camera vs marker, excluded")

    return out("exclude", EXCLUDED, "6:none", "excluded", "no corner markers visible")


def format_log(cam_id, d, visible_ids, solve_result=None):
    """One log line per refresh (spec: visible ids, per-marker error, moved, displaced, branch, reason, state)."""
    errs = " ".join(f"{i}:{e}px{'*' if i in d.moved else ''}" for i, e in sorted(d.errors.items())) or "-"
    delta = ""
    if solve_result is not None and d.action == "replace_pose":
        delta = f" pose delta {solve_result.get('delta_m', 0) * 100:.1f} cm/{solve_result.get('delta_deg', 0):.2f} deg"
    return (f"[{cam_id}] refresh: visible {sorted(visible_ids)} err {errs} (*=moved) displaced {sorted(d.displaced)} "
            f"branch {d.branch} -> {d.state} w={d.weight:g}{delta} | {'; '.join(d.reasons)}")
