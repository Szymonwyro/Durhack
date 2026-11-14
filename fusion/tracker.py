"""Person tracks for the fusion server: stable ids, constant-velocity Kalman, visible/predicted, pose skeleton.

    tracker = Tracker()
    tracker.camera_frame("camA", detections, now)   # world detections from one camera frame (see camera_worker)
    tracks = tracker.tracks(now)                     # list of dicts per docs/CONTRACTS.md (+ class_id/name for the map)

Association: each camera frame's person detections are matched to tracks by gated nearest neighbour (Hungarian),
then a Kalman update. Unmatched detections start new tracks. A track is `visible` if seen in the last 0.5 s, else
`predicted`. Dropped after 8 s predicted, or when its 2-sigma radius exceeds 3 m.

Skeleton (optional, from detections' `kp3d`): per track, the latest kp3d of each camera is kept; the source is the
camera with the highest mean joint conf (tie: larger bbox) among those seen in the last 0.3 s. Joints are smoothed
with an EMA (alpha 0.5). A null joint keeps its previous value for up to 0.5 s, then becomes null. While the track
is predicted, the last skeleton is translated with the Kalman displacement since last_seen and marked frozen; it is
dropped after 3 s frozen.
"""
import itertools
import math

import numpy as np
from scipy.optimize import linear_sum_assignment

PREDICT_AFTER_S = 0.5
DROP_PREDICTED_S = 8.0
DROP_2SIGMA_M = 3.0
GATE_M = 1.0              # max distance from a track (plus 2 sigma) to accept a detection
MEAS_SIGMA = 0.10         # m, detection noise
ACCEL_SIGMA = 0.8         # m/s^2, process noise (filtering between detections)
MAX_EXTRAPOLATE_S = 1.5   # predicted tracks keep moving with their velocity for at most this long, then hold
GROWTH = 0.35             # m/s, position sigma growth while extrapolating (same as quest/tools/bridge.py)
NEW_TRACK_SIGMA = 0.3     # m, initial position uncertainty; velocity starts at 0 +- 1 m/s

CONFIRM_HITS = 3          # detections before a new track is shown (blips / half-visible people never appear)
TENTATIVE_DROP_S = 0.3    # an unconfirmed track not seen for this long is deleted
SHORT_HITS = 10           # tracks with fewer detections in total (~0.3 s at 30 fps) ...
SHORT_DROP_S = 1.0        # ... are dropped after 1 s unseen instead of being predicted for 8 s
MERGE_M = 0.6             # a predicted track this close to a visible one is the same person -> dropped
SAME_FRAME_M = 0.4        # detections from one camera frame closer than this are one person (duplicate boxes)

LAG_SIGMA_PER_S = 0.5     # m of extra measurement noise per second of lag (late detections count a bit less)
MAX_LAG_S = 1.0           # detections older than this (vs the track) are shifted at most this far

SKEL_SOURCE_WINDOW_S = 0.3
SKEL_ALPHA = 0.5
SKEL_JOINT_HOLD_S = 0.5
SKEL_FROZEN_DROP_S = 3.0

_ids = itertools.count(1)


def valid_kp3d(kp3d):
    """17 entries, each None or 4 finite numbers."""
    return (isinstance(kp3d, list) and len(kp3d) == 17
            and all(j is None or (isinstance(j, list) and len(j) == 4
                                  and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                                          for v in j))
                    for j in kp3d)
            and any(j is not None for j in kp3d))


class Track:
    def __init__(self, pos, t, cam):
        self.id = None              # assigned when confirmed (CONFIRM_HITS), so blips don't use up ids
        self.hits = 1
        self.x = np.array([pos[0], pos[1], 0.0, 0.0])
        self.P = np.diag([NEW_TRACK_SIGMA ** 2] * 2 + [1.0] * 2)
        self.t = t                  # time of the state estimate
        self.last_seen = t          # capture time of the latest detection
        self.last_recv = t          # arrival time of the latest detection (drives visible/predicted)
        self.pos_at_last_seen = self.x[:2].copy()
        self.seen = {cam: t}        # camera -> last time it saw this track
        # skeleton
        self.skel_by_cam = {}       # cam -> (t, kp3d, mean_conf, bbox_area)
        self.skel = None            # 17 x ([x,y,z,conf] | None), smoothed
        self.joint_t = [0.0] * 17   # last time each joint was updated
        self.skel_src = None
        self.skel_t = 0.0           # time the source camera last saw the joints

    # ---- Kalman
    def predict(self, t):
        dt = t - self.t
        if dt <= 0:
            return
        F = np.eye(4)
        F[0, 2] = F[1, 3] = dt
        q = ACCEL_SIGMA ** 2
        G = np.array([[dt * dt / 2, 0], [0, dt * dt / 2], [dt, 0], [0, dt]])
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + G @ G.T * q
        self.t = t

    def pos_at(self, t):
        """Position at time t (may be slightly in the past: a late camera's capture time)."""
        return self.x[:2] + self.x[2:] * float(np.clip(t - self.t, -MAX_LAG_S, MAX_EXTRAPOLATE_S))

    def update(self, z, t, cam, recv=None):
        """z measured at capture time t. A detection older than the filter state (a slow camera, after a fast one
        already updated the track) is moved forward along the track's velocity to the filter time, and trusted
        a little less, instead of dragging the track back to where the person was."""
        lag = self.t - t
        z = np.asarray(z, float)
        if lag > 0:
            lag = min(lag, MAX_LAG_S)
            z = z + self.x[2:] * lag
            t = self.t
        else:
            lag = 0.0
        self.predict(t)
        H = np.zeros((2, 4))
        H[0, 0] = H[1, 1] = 1
        S = H @ self.P @ H.T + np.eye(2) * (MEAS_SIGMA ** 2 + (LAG_SIGMA_PER_S * lag) ** 2)
        K = self.P @ H.T @ np.linalg.inv(S)
        self.x = self.x + K @ (np.asarray(z) - H @ self.x)
        self.P = (np.eye(4) - K @ H) @ self.P
        self.last_seen = max(self.last_seen, t)
        self.last_recv = max(self.last_recv, recv if recv is not None else t)
        self.pos_at_last_seen = self.x[:2].copy()
        self.seen[cam] = t
        self.hits += 1
        if self.id is None and self.hits >= CONFIRM_HITS:
            self.id = next(_ids)

    def state_at(self, now):
        """(pos, vel, cov) extrapolated to `now` without changing the filter: constant velocity, position sigma
        growing linearly (GROWTH m/s), so a predicted track's 2-sigma reaches 3 m after ~4 s."""
        dt = max(0.0, now - self.t)
        pos = self.x[:2] + self.x[2:] * min(dt, MAX_EXTRAPOLATE_S)
        cov = self.P[:2, :2] + np.eye(2) * (GROWTH * dt) ** 2
        return pos, self.x[2:], cov

    # ---- skeleton
    def add_skeleton(self, cam, kp3d, bbox, t):
        if not valid_kp3d(kp3d):
            return
        confs = [j[3] for j in kp3d if j is not None]
        if not confs:
            return
        area = float(bbox[2] * bbox[3]) if bbox and len(bbox) == 4 else 0.0
        self.skel_by_cam[cam] = (t, kp3d, float(np.mean(confs)), area)
        recent = [(c, v) for c, v in self.skel_by_cam.items() if t - v[0] <= SKEL_SOURCE_WINDOW_S]
        src, (st, kp, _, _) = max(recent, key=lambda cv: (cv[1][2], cv[1][3]))
        if src != cam:  # a better view seen recently: keep its joints, this one isn't used
            return
        if self.skel is None:
            self.skel = [None] * 17
        for i in range(17):
            new = kp[i]
            if new is not None:
                old = self.skel[i]
                if old is None or t - self.joint_t[i] > SKEL_JOINT_HOLD_S:
                    self.skel[i] = list(new)
                else:
                    self.skel[i] = [round(SKEL_ALPHA * n + (1 - SKEL_ALPHA) * o, 3) for n, o in zip(new, old)]
                self.joint_t[i] = t
        self.skel_src, self.skel_t = src, st

    def skeleton_msg(self, now, predicted, pos_now):
        if self.skel is None:
            return None
        frozen = predicted
        if frozen and now - self.last_seen > SKEL_FROZEN_DROP_S:
            return None
        if frozen:
            dx, dy = pos_now[0] - self.pos_at_last_seen[0], pos_now[1] - self.pos_at_last_seen[1]
        else:
            dx = dy = 0.0
        kp = []
        for i, j in enumerate(self.skel):
            # live: joint seen in the last 0.5 s. frozen: joints that were still shown when the track was last seen
            ref = self.last_seen if frozen else now
            if j is None or ref - self.joint_t[i] > SKEL_JOINT_HOLD_S:
                kp.append(None)
            else:
                kp.append([round(j[0] + dx, 3), round(j[1] + dy, 3), j[2], j[3]])
        if all(j is None for j in kp):
            return None
        return {"kp": kp, "src": self.skel_src, "age": round(max(0.0, now - self.skel_t), 2), "frozen": frozen}


class Tracker:
    def __init__(self):
        self.tracks_ = []

    def camera_frame(self, cam, detections, now, t_capture=None):
        """detections: world detections from one camera frame (dicts with position, class_name, bbox, kp3d?).
        now = arrival time; t_capture = when the frame was taken (server clock), if known."""
        t = now if t_capture is None else min(t_capture, now)
        people = []  # one camera can't see one person twice: merge duplicate boxes (keep the most confident)
        for d in sorted((d for d in detections if d.get("class_name", "person") == "person"),
                        key=lambda d: -float(d.get("confidence", d.get("conf", 0.0)))):
            if all(math.dist(d["position"], q["position"]) >= SAME_FRAME_M for q in people):
                people.append(d)
        if self.tracks_ and people:
            cost = np.full((len(self.tracks_), len(people)), 1e6)
            for i, tr in enumerate(self.tracks_):
                _, _, cov = tr.state_at(now)  # extrapolated; the filter itself only steps on updates
                pos = tr.pos_at(t)            # where the track was when this frame was captured
                gate = GATE_M + 2 * math.sqrt(max(cov[0, 0], cov[1, 1]))
                for j, d in enumerate(people):
                    dist = math.dist(pos, d["position"])
                    if dist <= gate:
                        cost[i, j] = dist / gate
            rows, cols = linear_sum_assignment(cost)
            matched = {(r, c) for r, c in zip(rows, cols) if cost[r, c] < 1e6}
        else:
            matched = set()
        used = set()
        for r, c in matched:
            tr, d = self.tracks_[r], people[c]
            tr.update(d["position"], t, cam, recv=now)
            tr.add_skeleton(cam, d.get("kp3d"), d.get("bbox"), now)
            used.add(c)
        for c, d in enumerate(people):
            if c not in used:
                tr = Track(d["position"], t, cam)
                tr.last_recv = now
                tr.add_skeleton(cam, d.get("kp3d"), d.get("bbox"), now)
                self.tracks_.append(tr)

    def tracks(self, now):
        out, keep = [], []
        # older confirmed tracks first: when two overlap, the older id survives
        self.tracks_.sort(key=lambda tr: (tr.id is None, tr.id or 0))
        visible_pos = []
        for tr in self.tracks_:
            pos, vel, cov = tr.state_at(now)
            unseen = now - tr.last_recv  # by arrival: a camera with 0.3 s lag still counts as "seeing" them
            if tr.id is None:  # tentative: never shown; deleted if it doesn't get confirmed quickly
                if unseen <= TENTATIVE_DROP_S:
                    keep.append(tr)
                continue
            predicted = unseen > PREDICT_AFTER_S
            if not predicted and any(math.dist(pos, v) < MERGE_M for v in visible_pos):
                continue  # duplicate of an older visible track
            if predicted and (unseen > DROP_PREDICTED_S + PREDICT_AFTER_S
                              or (tr.hits < SHORT_HITS and unseen > SHORT_DROP_S)
                              or 2 * math.sqrt(max(cov[0, 0], cov[1, 1])) > DROP_2SIGMA_M
                              or any(math.dist(pos, v) < MERGE_M for v in visible_pos)):
                continue
            keep.append(tr)
            if not predicted:
                visible_pos.append(pos)
            msg = {"id": tr.id, "state": "predicted" if predicted else "visible",
                   "class_id": 0, "class_name": "person",
                   "pos": [round(float(pos[0]), 3), round(float(pos[1]), 3)],
                   "vel": [round(float(vel[0]), 3), round(float(vel[1]), 3)],
                   "cov": [[round(float(cov[0, 0]), 4), round(float(cov[0, 1]), 4)],
                           [round(float(cov[1, 0]), 4), round(float(cov[1, 1]), 4)]],
                   "last_seen": round(tr.last_seen, 3),
                   "seen_by": sorted(c for c, t in tr.seen.items() if tr.last_seen - t <= PREDICT_AFTER_S)}
            skel = tr.skeleton_msg(now, predicted, pos)
            if skel is not None:
                msg["skeleton"] = skel
            out.append(msg)
        self.tracks_ = keep
        return out
