"""Camera calibration-refresh inside a running camera worker: pose + state (TRUSTED/DEGRADED/EXCLUDED) from the
corner markers, decided by calib/refresh.py.

    auto = AutoPose("camB", K, dist, thresholds, initial=saved_pose)   # kept across camera reconnects
    auto.reconnected()                    # call whenever the camera is (re)opened: next check uses the fast path
    for frame in frames:
        if auto.feed(frame): calibration.update(R=auto.pose["R"], t=auto.pose["t"])
        auto.state, auto.weight           # TRUSTED 1.0 / DEGRADED degraded_weight / EXCLUDED 0

A check is due right after (re)connecting, then every refresh_s (TRUSTED), excluded_retry_s (EXCLUDED, DEGRADED after
a poor solve) or verify_retry_s (reconnected with no markers in view). Each check uses a few fresh frames (median
corners), projects each visible marker's known world corners with the CURRENT pose, optionally solves, and applies
decide_refresh(). Nothing is written to config/cameras.json (use calib.position to save a pose).
"""
import math
import time

import cv2
import numpy as np

from calib.config_io import load_markers
from calib.extrinsics import detect, make_detector, marker_world_corners, solve
from calib.refresh import DEGRADED, EXCLUDED, OFFLINE, decide_refresh, format_log, weight_for

CHECK_FRAMES = 3      # fresh frames per check (median corners); small so the reconnect fast path stays ~1 s


class AutoPose:
    def __init__(self, cam_id, K, dist, thresholds, log=print, initial=None):
        self.cam_id, self.K, self.dist = cam_id, np.asarray(K, np.float64), np.asarray(dist, np.float64)
        self.th, self.log = thresholds, log
        m = load_markers()
        self.world = {x["id"]: marker_world_corners(x, x.get("size_m", m["size_m"])) for x in m["markers"]}
        self.detector = make_detector(m.get("dictionary", "DICT_4X4_50"))
        self.pose = None
        if initial and initial.get("R") is not None:
            self.pose = {"R": initial["R"], "t": initial["t"], "pos": initial.get("pos"), "yaw": initial.get("yaw"),
                         "err": None, "ids": [], "time": None, "source": "saved"}
        self.state, self.weight, self.reasons = OFFLINE, 0.0, ["not started"]
        self.displaced = set()
        self.context = "reconnect"
        self.next_check = 0.0
        self.hist, self.frames = {}, 0
        self.last_decision = None
        self.last_check_at = None
        self.last_log = ""

    def reconnected(self):
        """Camera (re)opened: check on the next frames with the reconnect fast path."""
        self.context, self.next_check, self.hist, self.frames = "reconnect", 0.0, {}, 0

    def request_refresh(self):
        """Schedule a fresh marker check on the next frames."""
        self.context, self.next_check, self.hist, self.frames = "routine", 0.0, {}, 0

    def set_offline(self, why):
        self.state, self.weight, self.reasons = OFFLINE, 0.0, [why]

    def feed(self, frame):
        """Returns True when the pose was replaced."""
        now = time.time()
        if now < self.next_check:
            return False
        for i, c in detect(self.detector, frame).items():
            if i in self.world:
                self.hist.setdefault(i, []).append(c)
        self.frames += 1
        if self.frames < CHECK_FRAMES:
            return False
        visible = {i: np.median(np.stack(cs), axis=0) for i, cs in self.hist.items() if len(cs) >= 2}
        self.hist, self.frames = {}, 0
        return self.check(visible, now)

    def check(self, visible, now=None):
        """visible: {id: 4x2 corners px}. Runs decide_refresh and applies it. Returns True if the pose changed."""
        now = time.time() if now is None else now
        expected = self._expected(visible) if self.pose is not None else None
        tol = self.th["pixel_tol"]
        usable = {}
        for i, c in visible.items():  # same re-admission rule as decide_refresh, to choose what to solve from
            back = expected is not None and i in self.displaced and float(np.mean(
                np.linalg.norm(np.asarray(c) - np.asarray(expected[i]), axis=1))) <= tol
            if i not in self.displaced or back:
                usable[i] = c
        solve_result, sol = None, None
        if len(usable) >= 3:
            sol = solve(usable, self.world, self.K, self.dist)
            if sol is not None and sol["pos"][2] > 0:
                dm, dd = self._delta(sol) if self.pose is not None else (0.0, 0.0)
                solve_result = {"ids": sorted(sol["ids"]), "err": float(sol["err"]), "delta_m": dm, "delta_deg": dd}
            else:
                sol = None
        d = decide_refresh({i: np.asarray(c).tolist() for i, c in visible.items()},
                           None if expected is None else {i: np.asarray(c).tolist() for i, c in expected.items()},
                           self.displaced, solve_result, self.state, self.context, self.th)
        self.last_check_at = now
        self.last_log = format_log(self.cam_id, d, list(visible), solve_result)
        self.log(self.last_log)
        changed = False
        if d.action == "replace_pose" and sol is not None:
            self.pose = {"R": np.round(sol["R"], 6).tolist(), "t": np.round(sol["t"], 4).tolist(),
                         "pos": np.round(sol["pos"], 3).tolist(), "yaw": round(sol["yaw"], 1),
                         "err": round(sol["err"], 2), "ids": sorted(sol["ids"]), "time": now, "source": "solve"}
            changed = True
        self.state, self.weight, self.reasons = d.state, d.weight, d.reasons
        self.displaced = set(d.displaced)
        if d.branch != "reconnect:no-markers":
            self.context = "routine"
        self.next_check = now + d.next_in_s
        self.last_decision = d
        return changed

    @property
    def accepts_detections(self):
        return self.state in ("TRUSTED", DEGRADED) and self.pose is not None

    def _expected(self, visible):
        rvec, _ = cv2.Rodrigues(np.asarray(self.pose["R"], np.float64))
        tvec = np.asarray(self.pose["t"], np.float64)
        out = {}
        for i in visible:
            p, _ = cv2.projectPoints(self.world[i], rvec, tvec, self.K, self.dist)
            out[i] = p.reshape(-1, 2)
        return out

    def _delta(self, sol):
        R0 = np.asarray(self.pose["R"], np.float64)
        c0 = -R0.T @ np.asarray(self.pose["t"], np.float64)
        d = float(np.linalg.norm(np.asarray(sol["pos"]) - c0))
        ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R0.T @ np.asarray(sol["R"])) - 1) / 2))))
        return d, ang


def weight_of(state, th):
    return weight_for(state, th)


__all__ = ["AutoPose", "EXCLUDED", "DEGRADED", "OFFLINE"]
