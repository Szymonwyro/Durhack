"""fusion/skeleton.py: multi-camera skeleton fusion.   python tests/test_skeleton_fusion.py

Synthetic cameras look at a known, non-flat 3D skeleton; each camera's joints are produced with the real worker
lifting (perception/pose.py), so they carry the true billboard depth error.
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion.skeleton as fs  # noqa: E402
from fusion.skeleton import TrackSkeleton  # noqa: E402
from fusion.tracker import Tracker  # noqa: E402
from perception.pose import foot_from_ankles, lift_keypoints  # noqa: E402

K = np.array([[1060.0, 0, 640], [0, 1060.0, 360], [0, 0, 1]])
DIST = np.zeros(5)
PERSON = np.array([0.75, 0.75])
CAMS = {"camA": np.array([3.25, 0.75, 1.9]), "camB": np.array([0.75, 3.25, 2.1])}   # 90 degrees apart


def look_at(cam_pos, target):
    z = np.asarray(target, float) - cam_pos
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1.0])
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    R = np.vstack([x, y, z])
    return R, -R @ cam_pos


def skeleton_3d(person=PERSON, arm_forward=0.35):
    """Standing person facing +x, arms reaching forward (so the body is NOT flat: billboard depth error)."""
    px, py = person
    j = {0: (0.05, 0, 1.65), 1: (0.07, 0.03, 1.69), 2: (0.07, -0.03, 1.69), 3: (0.0, 0.08, 1.66),
         4: (0.0, -0.08, 1.66), 5: (0, 0.2, 1.45), 6: (0, -0.2, 1.45), 7: (arm_forward * 0.6, 0.22, 1.25),
         8: (arm_forward * 0.6, -0.22, 1.25), 9: (arm_forward, 0.2, 1.2), 10: (arm_forward, -0.2, 1.2),
         11: (0, 0.12, 0.95), 12: (0, -0.12, 0.95), 13: (0.05, 0.12, 0.5), 14: (0.05, -0.12, 0.5),
         15: (0, 0.12, 0.09), 16: (0, -0.12, 0.09)}
    return np.array([[px + j[i][0], py + j[i][1], j[i][2]] for i in range(17)])


def observe(cam_id, truth, offset=(0.0, 0.0), swap_lr=False, outlier=None):
    """What the worker would send for this camera: (kp3d, foot). offset = calibration error of this camera (m)."""
    c = CAMS[cam_id]
    R, t = look_at(c, [PERSON[0], PERSON[1], 0.9])
    px, _ = cv2.projectPoints(truth, cv2.Rodrigues(R)[0], t, K, DIST)
    kps = [[float(u), float(v), 0.9] for u, v in px.reshape(-1, 2)]
    if swap_lr:
        for a, b in fs.LR_PAIRS:
            kps[a], kps[b] = kps[b], kps[a]
    if outlier is not None:  # (joint, dx px, dy px)
        kps[outlier[0]][0] += outlier[1]
        kps[outlier[0]][1] += outlier[2]
    calib = {"K": K.tolist(), "dist": DIST.tolist(), "R": R.tolist(), "t": t.tolist()}
    fx, fy = foot_from_ankles(kps)
    ray = R.T @ np.linalg.inv(K) @ np.array([fx, fy, 1.0])
    foot = (c + ray * (-c[2] / ray[2]))[:2]
    xs = np.array([k[0] for k in kps])
    ys = np.array([k[1] for k in kps])
    kp3d = lift_keypoints(kps, [xs.min(), ys.min(), np.ptp(xs), np.ptp(ys)], foot, calib)
    off = np.array([offset[0], offset[1]])
    kp3d = [[j[0] + off[0], j[1] + off[1], j[2], j[3]] for j in kp3d]
    return kp3d, (foot + off).tolist()


def setup():
    fs.CAMERAS.clear()
    for cam, pos in CAMS.items():
        fs.set_camera(cam, pos.tolist(), 1.0)


def errors(msg, truth):
    return np.array([np.linalg.norm(np.array(k[:3]) - truth[i]) for i, k in enumerate(msg["kp"]) if k is not None])


def run(frames=12, cams=("camA", "camB"), **kw):
    """Feed `frames` frames from the cameras; the track position is the mean of the cameras' feet (like the Kalman)."""
    setup()
    truth = skeleton_3d()
    sk = TrackSkeleton()
    t = 100.0
    for _ in range(frames):
        obs = {c: observe(c, truth, **kw.get(c, {})) for c in cams}
        track = np.mean([o[1] for o in obs.values()], axis=0)
        for c, (kp3d, foot) in obs.items():
            sk.add(c, kp3d, foot, t, track)
        t += 0.05
    return sk.msg(t, False, track, t), truth, sk, t


def test_two_cameras_fix_depth():
    single, truth, _, _ = run(cams=("camA",))
    fused, _, _, _ = run()
    e1, e2 = errors(single, truth), errors(fused, truth)
    assert fused["src"] == "fused:camA+camB" and single["src"] == "camA"
    assert e1.max() > 0.25, f"single camera should show the billboard depth error, got {e1.max():.3f}"
    assert e2.max() < 0.05, f"fused joint error {e2.max() * 100:.1f} cm"
    return e1.max(), e2.max()


def test_root_alignment_removes_calibration_offset():
    fused, truth, _, _ = run(camB={"offset": (0.15, 0.0)})          # camB's calibration is 15 cm off in x
    setup()
    # the track (mean of both feet) is 7.5 cm off, so compare the skeleton's SHAPE: joints relative to the ankle midpoint
    def shape(kp):
        root = (np.array(kp[15][:3]) + np.array(kp[16][:3])) / 2
        return np.array([np.array(k[:3]) - root for k in kp])
    truth_shape = truth - (truth[15] + truth[16]) / 2
    err = np.linalg.norm(shape(fused["kp"]) - truth_shape, axis=1)
    assert err.max() < 0.05, f"shape error with a 15 cm calibration offset: {err.max() * 100:.1f} cm"
    return err.max()


def test_left_right_swap_corrected():
    fused, truth, _, _ = run(camB={"swap_lr": True})
    e = errors(fused, truth)
    assert e.max() < 0.06, f"L/R swap from camB not corrected: {e.max() * 100:.1f} cm"
    assert fused["kp"][9][1] > fused["kp"][10][1]                    # left wrist is on the +y side


def test_outlier_joint_rejected():
    clean, truth, _, _ = run()
    # (1) a wrong joint in a direction both cameras see (height): rejected by the cross-camera gate, every frame
    fused, _, _, _ = run(camB={"outlier": (9, 0, 120)})             # camB's left wrist 120 px too low
    only_a, _, _, _ = run(cams=("camA",))                          # with camB's wrist rejected, the wrist = camA's
    leak = np.linalg.norm(np.array(fused["kp"][9][:3]) - np.array(only_a["kp"][9][:3]))
    assert leak < 0.05, f"vertical outlier leaked into the fusion: {leak * 100:.1f} cm"
    others = [i for i in range(17) if i != 9]
    assert errors(fused, truth)[others].max() < 0.05                 # every other joint is still fused correctly
    # (2) a one-frame sideways glitch (only camB sees that direction): rejected against the fused history
    setup()
    sk = TrackSkeleton()
    t = 400.0
    for frame in range(12):
        for c in ("camA", "camB"):
            bad = {"outlier": (9, 120, 0)} if (c == "camB" and frame == 10) else {}
            kp3d, foot = observe(c, truth, **bad)
            sk.add(c, kp3d, foot, t, PERSON)
        if frame == 9:
            before = sk.msg(t, False, PERSON, t)
        if frame == 10:
            glitch = sk.msg(t, False, PERSON, t)
        t += 0.05
    lateral = abs(glitch["kp"][9][0] - before["kp"][9][0])
    assert lateral < 0.05, f"one-frame glitch moved the joint {lateral * 100:.1f} cm"


def test_camera_dropout_no_jump():
    setup()
    truth = skeleton_3d()
    sk = TrackSkeleton()
    t, prev, worst = 200.0, None, 0.0
    for frame in range(60):
        if frame == 30:
            sk.drop_camera("camB")                                   # camB goes OFFLINE mid-sequence
        cams = ("camA", "camB") if frame < 30 else ("camA",)
        obs = {c: observe(c, truth) for c in cams}
        track = PERSON                                               # Kalman holds its position
        for c, (kp3d, foot) in obs.items():
            sk.add(c, kp3d, foot, t, track)
        msg = sk.msg(t, False, track, t)
        cur = np.array([k[:3] for k in msg["kp"]])
        if prev is not None and frame >= 20:                         # settled; the dropout is at frame 30
            worst = max(worst, float(np.linalg.norm(cur - prev, axis=1).max()))
        prev = cur
        t += 0.05
    assert msg["src"] == "camA"
    assert worst < 0.05, f"largest frame-to-frame joint jump across the dropout: {worst * 100:.1f} cm"
    return worst


def test_tracker_end_to_end():
    """Through the Tracker hooks: fused skeleton on the track, frozen when both cameras go, src labels."""
    setup()
    truth = skeleton_3d()
    tr = Tracker()
    t = 300.0
    for _ in range(15):
        for c in ("camA", "camB"):
            kp3d, foot = observe(c, truth)
            tr.camera_frame(c, [{"position": foot, "class_name": "person", "bbox": [0, 0, 100, 400], "kp3d": kp3d,
                                 "confidence": 0.9}], t)
        t += 0.05
    out = tr.tracks(t)
    assert len(out) == 1 and out[0]["skeleton"]["src"] == "fused:camA+camB" and not out[0]["skeleton"]["frozen"]
    assert errors(out[0]["skeleton"], truth).max() < 0.08
    tr.drop_camera("camB", t)
    kp3d, foot = observe("camA", truth)
    tr.camera_frame("camA", [{"position": foot, "class_name": "person", "bbox": [0, 0, 100, 400], "kp3d": kp3d,
                              "confidence": 0.9}], t + 0.05)
    assert tr.tracks(t + 0.05)[0]["skeleton"]["src"] == "camA"
    tr.drop_camera("camA", t + 0.1)
    o = tr.tracks(t + 1.0)[0]
    assert o["state"] == "predicted" and o["skeleton"]["frozen"]
    assert "skeleton" not in tr.tracks(t + 3.5)[0]


if __name__ == "__main__":
    e1, e2 = test_two_cameras_fix_depth()
    print(f"ok two cameras at 90 deg: single-camera max joint error {e1 * 100:.1f} cm -> fused {e2 * 100:.1f} cm")
    print(f"ok root alignment: 15 cm calibration offset -> shape error {test_root_alignment_removes_calibration_offset() * 100:.1f} cm")
    test_left_right_swap_corrected()
    print("ok left/right swap corrected")
    test_outlier_joint_rejected()
    print("ok outlier joint rejected")
    print(f"ok camera dropout: largest joint jump {test_camera_dropout_no_jump() * 100:.1f} cm")
    test_tracker_end_to_end()
    print("ok tracker end to end (fused -> single -> frozen -> dropped)")
    print("all skeleton fusion tests passed")
