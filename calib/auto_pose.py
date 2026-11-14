"""Automatic camera position from the corner markers, inside a running camera worker.

    pose = AutoPose("camB", K, dist, interval_s=60)
    for frame in frames:
        update = pose.feed(frame)        # cheap except during a solve window (~15 frames every interval_s)
        if update: calibration.update(update)   # {"R", "t", "pos", "yaw", "err", "ids"}

It starts from the saved pose (config/cameras.json) if there is one, solves at startup, then every interval_s.
Only a solve from ALL 4 corner markers updates the pose (subsets of 3 disagree by ~15 cm when a sheet is slightly
off). A 2-3 marker solve is used only if the camera has no pose at all; otherwise it just checks whether the camera
moved (> 5 cm / 2 deg -> logged: clear all 4 markers so it can re-solve).
Nothing is written to config/cameras.json, so laptops don't fight in git. Use calib.position to save a pose.
"""
import math
import time

import numpy as np

from calib.config_io import load_markers
from calib.extrinsics import detect, make_detector, marker_world_corners, solve

MAX_ERR_PX = 4.0
MOVE_M, MOVE_DEG = 0.05, 2.0


def _big(moved):
    return moved[0] > MOVE_M or moved[1] > MOVE_DEG


class AutoPose:
    def __init__(self, cam_id, K, dist, interval_s=60.0, window=30, log=print, initial=None):
        self.cam_id, self.K, self.dist = cam_id, np.asarray(K, np.float64), np.asarray(dist, np.float64)
        self.interval_s, self.window, self.log = interval_s, window, log
        m = load_markers()
        self.world = {x["id"]: marker_world_corners(x, x.get("size_m", m["size_m"])) for x in m["markers"]}
        self.detector = make_detector(m.get("dictionary", "DICT_4X4_50"))
        self.pose = None  # current pose dict; `initial` = saved pose {"R", "t", "pos"} (not counted as 'ready')
        self.saved = initial
        self.next_solve = 0.0
        self.hist, self.frames = {}, 0

    @property
    def ready(self):
        return self.pose is not None

    def _current(self):
        return self.pose or self.saved

    def _good(self, sol):
        return (sol is not None and sol["pos"][2] > 0 and not sol["ambiguous"] and sol["err"] <= MAX_ERR_PX
                and len(sol["ids"]) >= 2)

    def feed(self, frame):
        """Returns a new pose dict when one is accepted, else None."""
        now = time.time()
        if now < self.next_solve:
            return None
        for i, c in detect(self.detector, frame).items():
            if i in self.world:
                self.hist.setdefault(i, []).append(c)
        self.frames += 1
        if self.frames < self.window:
            return None
        avg = {i: np.median(np.stack(cs), axis=0) for i, cs in self.hist.items() if len(cs) >= self.window // 4}
        sol = solve(avg, self.world, self.K, self.dist) if avg else None
        self.hist, self.frames = {}, 0
        if not self._good(sol):
            why = "no corner markers seen" if sol is None else f"{sorted(sol['ids'])}, {sol['err']:.1f} px"
            if self._current() is None:
                self.log(f"[{self.cam_id}] auto-pose: waiting for a good view of the corner markers ({why})")
                return None  # keep trying every window until the first good pose
            self.log(f"[{self.cam_id}] auto-pose: check not good ({why}); keeping the "
                     f"{'previous' if self.ready else 'saved'} pose")
            self.next_solve = now + self._retry()
            return None
        moved = self._moved(sol) if self._current() else None
        if len(sol["ids"]) < len(self.world) and moved is not None:
            # partial view (people hiding markers): too noisy to update from, only used to spot a moved camera
            if _big(moved):
                self.log(f"[{self.cam_id}] auto-pose: only markers {sorted(sol['ids'])} visible; that pose is "
                         f"{moved[0] * 100:.0f} cm / {moved[1]:.1f} deg off the current one. Keeping the current "
                         f"pose. If the camera moved, clear all 4 corner markers so it can re-solve.")
            self.next_solve = now + self._retry()
            return None
        was_ready = self.ready
        self.pose = {"R": np.round(sol["R"], 6).tolist(), "t": np.round(sol["t"], 4).tolist(),
                     "pos": np.round(sol["pos"], 3).tolist(), "yaw": round(sol["yaw"], 1),
                     "err": round(sol["err"], 2), "ids": sorted(sol["ids"]), "time": now}
        x, y, z = self.pose["pos"]
        if moved is None or not was_ready:
            self.log(f"[{self.cam_id}] auto-pose: calibrated from markers {self.pose['ids']}, {sol['err']:.1f} px, "
                     f"camera at ({x:.2f}, {y:.2f}) h {z:.2f} m, yaw {sol['yaw']:.0f}")
        elif _big(moved):
            self.log(f"[{self.cam_id}] auto-pose: CAMERA MOVED {moved[0] * 100:.0f} cm / {moved[1]:.1f} deg; "
                     f"updated to ({x:.2f}, {y:.2f}) h {z:.2f} m, {sol['err']:.1f} px")
        self.next_solve = now + self.interval_s
        return self.pose

    def _retry(self):
        """Until the first good solve, retry every 5 s; afterwards every interval."""
        return self.interval_s if self.ready else 5.0

    def _moved(self, sol):
        cur = self._current()
        d = float(np.linalg.norm(np.asarray(sol["pos"]) - np.asarray(cur["pos"])))
        R0, R1 = np.asarray(cur["R"]), np.asarray(sol["R"])
        ang = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(R0.T @ R1) - 1) / 2))))
        return d, ang
