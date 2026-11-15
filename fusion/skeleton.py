"""Per-track pose skeleton fused from several cameras (server side; only runs when workers send `kp3d`, i.e. --pose).

Each camera lifts joints onto a vertical "billboard" plane through the person's foot (perception/pose.py): a lifted
joint lies on its true camera ray, so it is precise across the view and wrong only in depth. Two cameras at different
angles therefore pin each joint down. Per track:

  a. root alignment   each camera's joints are taken relative to that detection's own root (its foot point, moved
                      back onto the ankle ray when the foot came from the ankles), then re-anchored at the fused
                      Kalman track position: calibration offsets between cameras cancel.
  b. uncertainty      per joint and camera: cov = s_perp^2 I + (s_ray^2 - s_perp^2) r r^T  (r = camera ray),
                      scaled by 1 / (keypoint conf x camera weight x join ramp).
  c. left/right       per L/R pair, swap a camera's two joints if that fits the fused history (or the best other
                      camera) clearly better.
  d. outlier gate     drop a camera's joint when its Mahalanobis distance to the others' combination is > 3.
  e. fuse             information form, then EMA (alpha 0.5); a joint with no estimate is held 0.5 s, then null.
                      While several cameras agree, each camera's per-joint depth correction is learned; when a
                      camera is left alone that correction decays over ~2 s, so nothing pops.
  f. dropout          OFFLINE/EXCLUDED camera: removed at once. No camera left: the skeleton freezes, moves with
                      the Kalman prediction, and is dropped after 3 s.

Time alignment: joints are stored relative to the root, so a person's translation between two cameras' frames
cancels; candidates older than 0.3 s are ignored.

    set_camera("camA", [x, y, z], weight)     # server: camera centre + state weight, on every message
    sk = TrackSkeleton()
    sk.add("camA", kp3d, foot_xy, t, track_xy)
    sk.drop_camera("camA")
    sk.msg(now, predicted, track_xy_now, last_seen)   # -> {"kp", "src", "age", "frozen"} or None
"""
import math

import numpy as np

SIGMA_PERP = 0.04        # m, across the camera's view
SIGMA_RAY = 0.40         # m, along the camera ray (billboard depth)
SIGMA_ISO = 0.15         # m, when the camera centre is unknown
ANKLE_HEIGHT = 0.09      # m, ankle joint above the floor (root correction when the foot came from the ankles)
ANKLE_FOOT_CONF = 0.4    # same rule as perception/pose.py foot_from_ankles
CANDIDATE_MAX_AGE_S = 0.3
EMA_ALPHA = 0.5
JOINT_HOLD_S = 0.5
FROZEN_DROP_S = 3.0
OUTLIER_MAHALANOBIS = 3.0
GLITCH_FRAMES = 3        # a joint that jumps away from the fused history is ignored for at most this many frames
SWAP_MARGIN = 0.7        # swap L/R only if the swapped error is below this fraction of the unswapped one
JOIN_RAMP_S = 0.5        # a camera's weight ramps in over this long
BIAS_ALPHA = 0.3         # learning rate of a camera's per-joint correction while fused with others
BIAS_DECAY_S = 2.0       # that correction fades over this long once the camera is alone
LR_PAIRS = [(5, 6), (7, 8), (9, 10), (11, 12), (13, 14), (15, 16)]
L_ANKLE, R_ANKLE = 15, 16

CAMERAS = {}  # cam id -> {"pos": np.array([x, y, z]) | None, "weight": float}


def set_camera(cam, pos, weight=1.0):
    ok = isinstance(pos, (list, tuple)) and len(pos) == 3 and all(isinstance(v, (int, float)) for v in pos)
    CAMERAS[cam] = {"pos": np.asarray(pos, float) if ok else None, "weight": float(weight)}


def joint_cov(point, cam_pos, conf, weight):
    """3x3 covariance of a billboard-lifted joint at `point` (absolute, as lifted) seen from cam_pos."""
    scale = 1.0 / max(1e-3, float(conf) * float(weight))
    if cam_pos is None:
        return np.eye(3) * SIGMA_ISO ** 2 * scale
    r = np.asarray(point, float) - cam_pos
    n = np.linalg.norm(r)
    if n < 1e-6:
        return np.eye(3) * SIGMA_ISO ** 2 * scale
    r /= n
    return (np.eye(3) * SIGMA_PERP ** 2 + (SIGMA_RAY ** 2 - SIGMA_PERP ** 2) * np.outer(r, r)) * scale


def fuse_estimates(xs, covs):
    """Information-form fusion of 3D estimates. Returns (x, cov)."""
    info = np.zeros((3, 3))
    vec = np.zeros(3)
    for x, c in zip(xs, covs):
        ci = np.linalg.inv(c)
        info += ci
        vec += ci @ x
    cov = np.linalg.inv(info)
    return cov @ vec, cov


def mahalanobis(a, b, cov):
    d = np.asarray(a) - np.asarray(b)
    return float(math.sqrt(max(0.0, d @ np.linalg.solve(cov, d))))


def root_of(kp3d, foot, cam_pos):
    """Horizontal root of one camera's skeleton. The worker's foot is the ankle midpoint projected to the floor
    along the camera ray, i.e. pushed away from the camera by ankle_height / tan(elevation); undo that."""
    foot = np.asarray(foot, float)
    la, ra = kp3d[L_ANKLE], kp3d[R_ANKLE]
    from_ankles = la is not None and ra is not None and la[3] > ANKLE_FOOT_CONF and ra[3] > ANKLE_FOOT_CONF
    if cam_pos is None or not from_ankles or cam_pos[2] <= ANKLE_HEIGHT:
        return foot
    return foot + (cam_pos[:2] - foot) * (ANKLE_HEIGHT / cam_pos[2])


class TrackSkeleton:
    def __init__(self):
        self.cands = {}       # cam -> {"t", "rel": 17 x (np3 | None), "abs": lifted joints, "conf", "root_off", "since"}
        self.rel = [None] * 17    # fused + smoothed joints, relative to the anchor (xy) / absolute z
        self.conf = [0.0] * 17
        self.cov = [None] * 17    # covariance of each fused joint (how sure the history is, and in which direction)
        self.joint_t = [0.0] * 17
        self.bias = {}        # cam -> 17 x (np3 | None): fused - camera estimate, learned while several cameras agree
        self.bias_t = {}      # cam -> 17 x time the bias was last learned
        self.anchor_off = np.zeros(2)   # anchor = track position + this (mean root correction)
        self.src, self.src_t = None, 0.0
        self.glitch = {}      # (cam, joint) -> consecutive frames rejected against the history
        self.raw = {}         # cam -> 17 x ([x, y, z] rel | None), for the map's debug view

    # ---- input
    def add(self, cam, kp3d, foot, t, track_xy=None):
        info = CAMERAS.get(cam, {"pos": None, "weight": 1.0})
        cam_pos = info["pos"]
        root = root_of(kp3d, foot, cam_pos)
        rel, absolute, conf = [], [], []
        for j in kp3d:
            if j is None:
                rel.append(None), absolute.append(None), conf.append(0.0)
            else:
                p = np.asarray(j[:3], float)
                absolute.append(p)
                rel.append(np.array([p[0] - root[0], p[1] - root[1], p[2]]))
                conf.append(float(j[3]))
        prev = self.cands.get(cam)
        since = prev["since"] if prev is not None and t - prev["t"] <= JOINT_HOLD_S else t
        self.cands[cam] = {"t": t, "rel": rel, "abs": absolute, "conf": conf, "since": since,
                           "root_off": root - np.asarray(foot, float)}
        self.fuse(t)

    def drop_camera(self, cam):
        self.cands.pop(cam, None)
        self.raw.pop(cam, None)

    # ---- fusion
    def fuse(self, t):
        cams = {c: v for c, v in self.cands.items() if t - v["t"] <= CANDIDATE_MAX_AGE_S}
        if not cams:
            return
        # join ramp: a camera that joins an already-running fusion eases in over JOIN_RAMP_S. Relative to the
        # longest-present camera, so cameras that start together are not down-weighted. Only for blending.
        ramps = {c: min(1.0, max(0.05, (t - v["since"]) / JOIN_RAMP_S)) for c, v in cams.items()}
        top = max(ramps.values())
        weights, base = {}, {}
        for c in cams:
            base[c] = max(1e-3, CAMERAS.get(c, {"weight": 1.0})["weight"])
            weights[c] = base[c] * ramps[c] / top
        cam_pos = {c: CAMERAS.get(c, {"pos": None})["pos"] for c in cams}

        def cov(c, i, w=None):
            v = cams[c]
            return joint_cov(v["abs"][i], cam_pos[c], max(v["conf"][i], 0.05), (w or weights)[c])

        def check_cov(c, i):
            return cov(c, i, base)

        # c. left/right check per camera and pair
        for c, v in cams.items():
            for a, b in LR_PAIRS:
                if v["rel"][a] is None or v["rel"][b] is None:
                    continue
                ref = self._reference(c, a, b, cams, base, t, check_cov)
                if ref is None:
                    continue
                ra, rb, sa, sb = ref  # reference joints + their covariances (the reference has depth error too)
                ca, cb = check_cov(c, a), check_cov(c, b)
                keep = mahalanobis(v["rel"][a], ra, ca + sa) + mahalanobis(v["rel"][b], rb, cb + sb)
                swap = mahalanobis(v["rel"][a], rb, ca + sb) + mahalanobis(v["rel"][b], ra, cb + sa)
                if swap < SWAP_MARGIN * keep:
                    for key in ("rel", "abs", "conf"):
                        v[key][a], v[key][b] = v[key][b], v[key][a]

        used = set()
        for i in range(17):
            est = [(c, v["rel"][i], cov(c, i)) for c, v in cams.items() if v["rel"][i] is not None]
            if not est:
                continue
            # d. outlier gate. First against the fused history: a one-frame glitch in a direction the other
            # cameras can't see (their depth) is caught here; real motion gets through after GLITCH_FRAMES.
            if self.rel[i] is not None and t - self.joint_t[i] <= JOINT_HOLD_S and self.cov[i] is not None:
                kept = []
                for c, x, sc in est:
                    far = mahalanobis(x, self.rel[i], check_cov(c, i) + self.cov[i]) > OUTLIER_MAHALANOBIS
                    n = self.glitch.get((c, i), 0) + 1 if far else 0
                    self.glitch[(c, i)] = n
                    if not far or n > GLITCH_FRAMES:
                        kept.append((c, x, sc))
                if not kept:
                    continue  # hold the joint this frame
                est = kept
            if len(est) == 2:
                (c1, x1, s1), (c2, x2, s2) = est
                if mahalanobis(x1, x2, s1 + s2) > OUTLIER_MAHALANOBIS:
                    if self.rel[i] is not None and t - self.joint_t[i] <= JOINT_HOLD_S:
                        d1, d2 = mahalanobis(x1, self.rel[i], s1), mahalanobis(x2, self.rel[i], s2)
                    else:  # no history: keep the more confident view
                        d1, d2 = -cams[c1]["conf"][i] * weights[c1], -cams[c2]["conf"][i] * weights[c2]
                    est = [est[0] if d1 <= d2 else est[1]]
            elif len(est) > 2:
                kept = []
                for k, (c, x, s) in enumerate(est):
                    others = [e for m, e in enumerate(est) if m != k]
                    xo, so = fuse_estimates([e[1] for e in others], [e[2] for e in others])
                    if mahalanobis(x, xo, s + so) <= OUTLIER_MAHALANOBIS:
                        kept.append((c, x, s))
                est = kept or est
            # e. fuse (a lone camera gets its fading learned correction)
            if len(est) == 1:
                c, x, s = est[0]
                b = self.bias.get(c, [None] * 17)[i]
                if b is not None:
                    x = x + b * math.exp(-(t - self.bias_t[c][i]) / BIAS_DECAY_S)
                fused, fused_cov = x, s
            else:
                fused, fused_cov = fuse_estimates([e[1] for e in est], [e[2] for e in est])
                for c, x, _s in est:
                    bias = self.bias.setdefault(c, [None] * 17)
                    times = self.bias_t.setdefault(c, [0.0] * 17)
                    new = fused - x
                    bias[i] = new if bias[i] is None or t - times[i] > BIAS_DECAY_S else \
                        BIAS_ALPHA * new + (1 - BIAS_ALPHA) * bias[i]
                    times[i] = t
            used.update(e[0] for e in est)
            old = self.rel[i]
            if old is None or t - self.joint_t[i] > JOINT_HOLD_S:
                self.rel[i] = fused
            else:
                self.rel[i] = EMA_ALPHA * fused + (1 - EMA_ALPHA) * old
            self.cov[i] = fused_cov
            self.conf[i] = max(cams[e[0]]["conf"][i] for e in est)
            self.joint_t[i] = t
        if used:
            self.src = sorted(used)[0] if len(used) == 1 else "fused:" + "+".join(sorted(used))
            self.src_t = max(cams[c]["t"] for c in used)
            off = np.mean([cams[c]["root_off"] for c in used], axis=0)
            self.anchor_off = EMA_ALPHA * off + (1 - EMA_ALPHA) * self.anchor_off
        self.raw = {c: [None if p is None else p.tolist() for p in v["rel"]] for c, v in cams.items()}

    def _reference(self, cam, a, b, cams, weights, t, cov):
        """Where joints a, b should be (+ covariances): the fused history if fresh, else the best other camera."""
        if (self.rel[a] is not None and self.rel[b] is not None
                and t - self.joint_t[a] <= JOINT_HOLD_S and t - self.joint_t[b] <= JOINT_HOLD_S):
            return self.rel[a], self.rel[b], self.cov[a], self.cov[b]
        best, score = None, 0.0
        for c, v in cams.items():
            if c == cam or v["rel"][a] is None or v["rel"][b] is None:
                continue
            s = (v["conf"][a] + v["conf"][b]) * weights[c]
            mine = (cams[cam]["conf"][a] + cams[cam]["conf"][b]) * weights[cam]
            if s > score and s >= mine:  # only defer to a camera that is at least as sure
                best, score = (v["rel"][a], v["rel"][b], cov(c, a), cov(c, b)), s
        return best

    # ---- output
    def _abs(self, p, anchor):
        return [round(float(p[0] + anchor[0]), 3), round(float(p[1] + anchor[1]), 3), round(float(p[2]), 3)]

    def msg(self, now, predicted, track_xy, last_seen):
        if all(p is None for p in self.rel):
            return None
        if predicted and now - last_seen > FROZEN_DROP_S:
            return None
        ref = last_seen if predicted else now
        anchor = np.asarray(track_xy, float) + self.anchor_off
        kp = []
        for i, p in enumerate(self.rel):
            if p is None or ref - self.joint_t[i] > JOINT_HOLD_S:
                kp.append(None)
            else:
                kp.append(self._abs(p, anchor) + [round(self.conf[i], 2)])
        if all(j is None for j in kp):
            return None
        return {"kp": kp, "src": self.src, "age": round(max(0.0, now - self.src_t), 2), "frozen": bool(predicted)}

    def raw_msg(self, track_xy):
        """Each camera's own root-aligned joints (absolute), for the map's debug view."""
        anchor = np.asarray(track_xy, float) + self.anchor_off
        return {c: [None if p is None else self._abs(p, anchor) for p in kp] for c, kp in self.raw.items()}
