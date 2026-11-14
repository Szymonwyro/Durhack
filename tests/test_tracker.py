"""fusion/tracker.py: stable id, predicted state, skeleton smoothing/hold/frozen/drop.   python tests/test_tracker.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fusion.tracker import Tracker  # noqa: E402


def kp(x, y, conf=0.9, z_nose=1.65):
    k = [[x, y, 1.0, conf] for _ in range(17)]
    k[0] = [x, y, z_nose, conf]
    return k


def det(x, y, k=None, h=300):
    d = {"position": [x, y], "class_name": "person", "bbox": [0, 0, 100, h]}
    if k is not None:
        d["kp3d"] = k
    return d


def test_skeleton_lifecycle():
    tr = Tracker()
    t = 1000.0
    for i in range(10):  # walking +x at 1 m/s, 10 Hz, with skeleton
        tr.camera_frame("camA", [det(0.1 * i, 0.5, kp(0.1 * i, 0.5))], t + 0.1 * i)
    out = tr.tracks(t + 0.9)
    assert len(out) == 1 and out[0]["state"] == "visible" and "skeleton" in out[0]
    s = out[0]["skeleton"]
    assert not s["frozen"] and s["src"] == "camA" and abs(s["kp"][0][2] - 1.65) < 1e-6
    tid = out[0]["id"]
    # EMA: a jump of 0.2 m in nose height moves the smoothed value halfway
    tr.camera_frame("camA", [det(1.0, 0.5, kp(1.0, 0.5, z_nose=1.85))], t + 1.0)
    assert abs(tr.tracks(t + 1.0)[0]["skeleton"]["kp"][0][2] - 1.75) < 1e-6
    # detections without skeleton: joints held 0.5 s, then gone
    tr.camera_frame("camA", [det(1.1, 0.5)], t + 1.1)
    assert "skeleton" in tr.tracks(t + 1.1)[0]
    tr.camera_frame("camA", [det(1.6, 0.5)], t + 1.6)
    assert "skeleton" not in tr.tracks(t + 1.6)[0]
    # skeleton again, then person disappears -> predicted, frozen skeleton moves with the track
    tr.camera_frame("camA", [det(1.7, 0.5, kp(1.7, 0.5))], t + 1.7)
    o = tr.tracks(t + 2.7)[0]
    assert o["id"] == tid and o["state"] == "predicted" and o["skeleton"]["frozen"]
    moved = o["pos"][0] - 1.7
    assert moved > 0.3 and abs(o["skeleton"]["kp"][1][0] - (1.7 + moved)) < 0.05
    assert "skeleton" not in tr.tracks(t + 4.8)[0]   # > 3 s frozen -> dropped (track itself still predicted)


def test_best_view_source():
    tr = Tracker()
    t = 2000.0
    tr.camera_frame("camA", [det(1.0, 1.0, kp(1.0, 1.0, conf=0.5))], t)
    tr.camera_frame("camB", [det(1.02, 1.0, kp(1.02, 1.0, conf=0.9))], t + 0.05)
    tr.camera_frame("camA", [det(1.0, 1.0, kp(1.0, 1.0, conf=0.5))], t + 0.1)
    assert tr.tracks(t + 0.1)[0]["skeleton"]["src"] == "camB"   # higher mean conf within 0.3 s wins
    tr.camera_frame("camA", [det(1.0, 1.0, kp(1.0, 1.0, conf=0.5))], t + 0.5)
    assert tr.tracks(t + 0.5)[0]["skeleton"]["src"] == "camA"   # camB older than 0.3 s


if __name__ == "__main__":
    test_skeleton_lifecycle()
    test_best_view_source()
    print("all tracker tests passed")


def test_empty_frames_dont_inflate():
    tr = Tracker()
    t = 3000.0
    for i in range(5):
        tr.camera_frame("camA", [det(0.5, 0.5)], t + 0.1 * i)
    for i in range(30):  # 3 s of empty frames from a streaming camera
        tr.camera_frame("camA", [], t + 0.5 + 0.1 * i)
    o = tr.tracks(t + 3.4)
    assert o and o[0]["state"] == "predicted" and o[0]["cov"][0][0] < 1.6  # ~ (0.35*3)^2 + small


if __name__ == "__main__":
    test_empty_frames_dont_inflate()
    print("empty-frame test passed")
