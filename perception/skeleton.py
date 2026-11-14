"""Pose keypoints (pixels) -> 3D skeleton in world metres, by "billboard" lifting.

The person is approximated by a vertical plane through their foot point on the floor, facing the camera
(normal = horizontal direction from the camera centre to the foot). Each keypoint's camera ray is intersected
with that plane. Height and sideways position come out well; depth along the camera's view is approximate
(the body isn't flat), roughly +-0.2 m.

    kp3d = lift_keypoints(keypoints, bbox, foot_world, calibration)   # 17 x [x, y, z, conf] | None, or None

COCO-17 order (see docs/CONTRACTS.md): 0 nose ... 15 left_ankle, 16 right_ankle.
"""
from collections.abc import Sequence

import cv2
import numpy as np

JOINT_NAMES = ["nose", "left_eye", "right_eye", "left_ear", "right_ear", "left_shoulder", "right_shoulder",
               "left_elbow", "right_elbow", "left_wrist", "right_wrist", "left_hip", "right_hip", "left_knee",
               "right_knee", "left_ankle", "right_ankle"]
BONES = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14],
         [14, 16], [0, 5], [0, 6]]
NOSE, L_ANKLE, R_ANKLE = 0, 15, 16

JOINT_MIN_CONF = 0.3     # below this a joint is null
ANKLE_FOOT_CONF = 0.4    # both ankles above this -> foot point = mean of the ankles
MIN_BBOX_HEIGHT_PX = 120
MIN_VALID_JOINTS = 8


def foot_from_ankles(keypoints: Sequence[Sequence[float]] | None) -> list[float] | None:
    """Image foot point from the ankles [x, y], if both are confident; else None (caller uses the bbox)."""
    if keypoints is None or len(keypoints) < 17:
        return None
    la, ra = keypoints[L_ANKLE], keypoints[R_ANKLE]
    if la[2] > ANKLE_FOOT_CONF and ra[2] > ANKLE_FOOT_CONF:
        return [(la[0] + ra[0]) / 2, (la[1] + ra[1]) / 2]
    return None


def camera_centre(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    return -R.T @ t


def billboard_plane(foot_world: Sequence[float], centre: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """(point, unit normal) of the vertical plane through the foot, facing the camera; None if degenerate."""
    p = np.array([foot_world[0], foot_world[1], 0.0])
    n = p - centre
    n[2] = 0.0
    norm = np.linalg.norm(n)
    if norm < 1e-6:  # camera straight above the person
        return None
    return p, n / norm


def lift_pixels(pixels: np.ndarray, K, dist, R, t, plane) -> np.ndarray:
    """N x 2 pixels -> N x 3 world points on the plane (NaN where the ray misses / points away)."""
    R, t = np.asarray(R, np.float64), np.asarray(t, np.float64).reshape(3)
    norm = cv2.undistortPoints(np.asarray(pixels, np.float64).reshape(-1, 1, 2),
                               np.asarray(K, np.float64), np.asarray(dist, np.float64)).reshape(-1, 2)
    rays = (R.T @ np.column_stack([norm, np.ones(len(norm))]).T).T  # world directions
    c = camera_centre(R, t)
    p0, n = plane
    denom = rays @ n
    s = np.full(len(rays), np.nan)
    ok = np.abs(denom) > 1e-9
    s[ok] = ((p0 - c) @ n) / denom[ok]
    s[s <= 0] = np.nan
    return c + rays * s[:, None]


def lift_keypoints(keypoints, bbox, foot_world, calibration) -> list | None:
    """keypoints: 17 x [x, y, conf] (pixels). Returns 17 x ([x, y, z, conf] | None), or None if unusable."""
    if keypoints is None or len(keypoints) != 17 or foot_world is None or calibration.get("R") is None:
        return None
    if bbox[3] < MIN_BBOX_HEIGHT_PX:
        return None
    kp = np.asarray(keypoints, np.float64)
    valid = kp[:, 2] >= JOINT_MIN_CONF
    if valid.sum() < MIN_VALID_JOINTS:
        return None
    R, t = np.asarray(calibration["R"], np.float64), np.asarray(calibration["t"], np.float64).reshape(3)
    plane = billboard_plane(foot_world, camera_centre(R, t))
    if plane is None:
        return None
    pts = lift_pixels(kp[:, :2], calibration["K"], calibration["dist"], R, t, plane)
    out = []
    for i in range(17):
        if valid[i] and np.isfinite(pts[i]).all():
            out.append([round(float(pts[i][0]), 3), round(float(pts[i][1]), 3), round(float(pts[i][2]), 3),
                        round(float(kp[i, 2]), 2)])
        else:
            out.append(None)
    return out if sum(j is not None for j in out) >= MIN_VALID_JOINTS else None
