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
    for i in range(12):  # an established person (>= SHORT_HITS detections)
        tr.camera_frame("camA", [det(0.5, 0.5)], t + 0.04 * i)
    for i in range(30):  # 3 s of empty frames from a streaming camera
        tr.camera_frame("camA", [], t + 0.5 + 0.1 * i)
    o = tr.tracks(t + 3.4)
    assert o and o[0]["state"] == "predicted" and o[0]["cov"][0][0] < 1.6  # ~ (0.35*3)^2 + small


if __name__ == "__main__":
    test_empty_frames_dont_inflate()
    print("empty-frame test passed")


def test_no_ghosts():
    tr = Tracker()
    t = 4000.0
    tr.camera_frame("camA", [det(1.0, 1.0), det(0.2, 1.4)], t)             # blip at (0.2, 1.4): seen once
    for i in range(1, 20):
        tr.camera_frame("camA", [det(1.0, 1.0)], t + 0.033 * i)
    out = tr.tracks(t + 0.66)
    assert len(out) == 1 and out[0]["pos"][0] > 0.9                          # blip never shown
    for i in range(5):                                                        # half-visible person: 5 frames
        tr.camera_frame("camA", [det(1.0, 1.0), det(0.3, 0.3)], t + 0.7 + 0.033 * i)
    assert len(tr.tracks(t + 0.85)) == 2
    for i in range(40):
        tr.camera_frame("camA", [det(1.0, 1.0)], t + 0.9 + 0.033 * i)
    assert len(tr.tracks(t + 2.3)) == 1                                       # short track gone after 1 s unseen


if __name__ == "__main__":
    test_no_ghosts()
    print("ghost test passed")


def simulate_two_cameras(compensate):
    """Person walks +x at 1 m/s. camA: 30 fps, 0.03 s lag. camB: 6 fps, 0.3 s lag. Returns mean/max track error."""
    tr = Tracker()
    events = []
    for k in range(int(4 * 30)):
        tc = 5000.0 + k / 30
        events.append((tc + 0.03, "camA", tc))
    for k in range(int(4 * 6)):
        tc = 5000.0 + k / 6
        events.append((tc + 0.30, "camB", tc))
    errs = []
    for arrival, cam, tc in sorted(events):
        true_x = (tc - 5000.0) * 1.0
        tr.camera_frame(cam, [det(true_x, 0.75)], arrival, t_capture=tc if compensate else None)
        out = tr.tracks(arrival)
        if out and arrival > 5001.0:
            errs.append(abs(out[0]["pos"][0] - (arrival - 5000.0)))
    return sum(errs) / len(errs), max(errs)


def test_lag_compensation():
    mean_c, max_c = simulate_two_cameras(True)
    mean_n, max_n = simulate_two_cameras(False)
    assert mean_c < 0.05 and max_c < 0.12, (mean_c, max_c)
    assert mean_n > mean_c


if __name__ == "__main__":
    test_lag_compensation()
    for c in (False, True):
        m, x = simulate_two_cameras(c)
        print(f"{'with' if c else 'without'} lag compensation: track error mean {m * 100:.1f} cm, max {x * 100:.1f} cm")
