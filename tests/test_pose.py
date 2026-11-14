"""Billboard lifting maths: synthetic camera + known 3D points ON the billboard plane -> project -> lift -> < 2 cm.

    python tests/test_skeleton.py        (or: pytest tests/)
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from perception.pose import billboard_plane, camera_centre, foot_from_ankles, lift_keypoints  # noqa: E402

K = np.array([[1060.0, 0, 640], [0, 1060.0, 360], [0, 0, 1]])
DIST = np.array([0.17, -0.8, 0.0, 0.0, 0.88])  # realistic distortion (camA-like) to exercise undistortPoints


def look_at(cam_pos, target):
    """World->camera R, t for a camera at cam_pos looking at target (camera y = down in the image)."""
    z = np.asarray(target, float) - cam_pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return R, -R @ cam_pos


def person_on_plane(foot, centre):
    """17 joints on the billboard plane through `foot` (heights roughly human), plus the plane."""
    p0, n = billboard_plane(foot, centre)
    side = np.array([-n[1], n[0], 0.0])  # horizontal, in the plane
    layout = {0: (0, 1.65), 1: (-0.03, 1.69), 2: (0.03, 1.69), 3: (-0.08, 1.66), 4: (0.08, 1.66),
              5: (-0.2, 1.45), 6: (0.2, 1.45), 7: (-0.25, 1.15), 8: (0.25, 1.15), 9: (-0.27, 0.9),
              10: (0.27, 0.9), 11: (-0.12, 0.95), 12: (0.12, 0.95), 13: (-0.12, 0.5), 14: (0.12, 0.5),
              15: (-0.12, 0.08), 16: (0.12, 0.08)}
    return np.array([p0 + side * s + np.array([0, 0, h]) for s, h in (layout[i] for i in range(17))])


def check_case(cam_pos, foot):
    R, t = look_at(np.asarray(cam_pos, float), [foot[0], foot[1], 0.9])
    calib = {"K": K.tolist(), "dist": DIST.tolist(), "R": R.tolist(), "t": t.tolist()}
    joints = person_on_plane(foot, camera_centre(R, t))
    px, _ = cv2.projectPoints(joints, cv2.Rodrigues(R)[0], t, K, DIST)
    px = px.reshape(-1, 2)
    kps = [[float(u), float(v), 0.9] for u, v in px]
    bbox = [px[:, 0].min(), px[:, 1].min(), np.ptp(px[:, 0]), np.ptp(px[:, 1])]
    out = lift_keypoints(kps, bbox, foot, calib)
    assert out is not None, "skeleton rejected"
    err = np.linalg.norm(np.array([o[:3] for o in out]) - joints, axis=1)
    return float(err.max()), out


def test_lift_round_trip():
    for cam_pos, foot in [((2.66, 2.02, 1.64), (0.75, 0.75)),   # camA-like
                          ((0.79, -1.65, 2.22), (1.2, 0.3)),    # camB-like, high
                          ((-1.0, 0.5, 1.0), (1.4, 1.4))]:     # low, far
        worst, out = check_case(cam_pos, foot)
        assert worst < 0.02, f"max error {worst * 100:.2f} cm for camera {cam_pos}"
        assert 1.55 < out[0][2] < 1.75 and out[15][2] < 0.15


def test_rejections():
    R, t = look_at(np.array([2.66, 2.02, 1.64]), [0.75, 0.75, 0.9])
    calib = {"K": K.tolist(), "dist": DIST.tolist(), "R": R.tolist(), "t": t.tolist()}
    kps = [[600.0, 200 + 20 * i, 0.9] for i in range(17)]
    assert lift_keypoints(kps, [580, 200, 60, 100], (0.75, 0.75), calib) is None       # bbox < 120 px
    few = [[x, y, 0.9 if i < 7 else 0.1] for i, (x, y, _) in enumerate(kps)]
    assert lift_keypoints(few, [580, 200, 60, 340], (0.75, 0.75), calib) is None        # < 8 joints
    assert lift_keypoints(kps, [580, 200, 60, 340], (0.75, 0.75), {**calib, "R": None}) is None


def test_foot_from_ankles():
    kps = [[0, 0, 0.9]] * 15 + [[100, 400, 0.9], [120, 410, 0.8]]
    assert foot_from_ankles(kps) == [110, 405]
    kps[16] = [120, 410, 0.3]
    assert foot_from_ankles(kps) is None


if __name__ == "__main__":
    test_lift_round_trip()
    test_rejections()
    test_foot_from_ankles()
    for cam, foot in [((2.66, 2.02, 1.64), (0.75, 0.75)), ((0.79, -1.65, 2.22), (1.2, 0.3)), ((-1.0, 0.5, 1.0), (1.4, 1.4))]:
        print(f"camera {cam}, foot {foot}: max joint error {check_case(cam, foot)[0] * 1000:.2f} mm")
    print("all skeleton tests passed")
