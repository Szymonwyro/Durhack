"""Stage 2 (hard disconnect): camera registry + server purge + tracker.   python tests/test_cameras.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import fusion.server as server  # noqa: E402
from fusion.cameras import DEGRADED, EXCLUDED, OFFLINE, TRUSTED, CameraRegistry  # noqa: E402
from fusion.tracker import Tracker  # noqa: E402


def det(x, y, cam="camA"):
    return {"camera_id": cam, "class_id": 0, "class_name": "person", "confidence": 0.9, "bbox": [0, 0, 100, 300],
            "image_point": [0, 0], "position": [x, y]}


def test_registry_states():
    logs = []
    reg = CameraRegistry(["camA", "camB"], stale_timeout=2.5, log=logs.append)
    assert reg.cams["camA"]["state"] == OFFLINE and not reg.active("camA")
    assert reg.on_message("camA", {"state": TRUSTED, "weight": 1.0}, 100.0) == [("camA", OFFLINE, TRUSTED)]
    assert reg.active("camA") and reg.weight("camA") == 1.0
    reg.on_message("camA", {"state": DEGRADED, "weight": 0.4}, 100.5)
    assert reg.weight("camA") == 0.4
    reg.on_message("camA", {"state": EXCLUDED, "weight": 0.7}, 101.0)       # EXCLUDED always weight 0
    assert not reg.active("camA") and reg.cams["camA"]["weight"] == 0.0
    reg.on_message("camB", {}, 101.0)                                          # old worker: TRUSTED + warning
    assert reg.cams["camB"]["state"] == TRUSTED and any("old worker" in m for m in logs)
    reg.on_message("camB", {"type": "status", "state": OFFLINE}, 101.5)       # worker's final status
    assert reg.cams["camB"]["state"] == OFFLINE


def test_hung_worker_goes_offline():
    reg = CameraRegistry(["camA"], stale_timeout=2.5, log=lambda m: None)
    reg.on_message("camA", {"state": TRUSTED, "weight": 1.0}, 200.0)          # then silence, socket still open
    assert reg.check_stale(202.4) == []
    assert reg.check_stale(202.6) == [("camA", TRUSTED, OFFLINE)]
    assert not reg.active("camA")


def test_sole_track_goes_predicted_shared_stays():
    tr = Tracker()
    t = 1000.0
    for i in range(15):  # person 1 seen by camA only; person 2 by camA and camB
        tr.camera_frame("camA", [det(0.2 + 0.03 * i, 0.3), det(1.2, 1.2)], t + 0.04 * i)
        tr.camera_frame("camB", [det(1.21, 1.2, "camB")], t + 0.04 * i + 0.01)
    before = {o["pos"][1] > 1: o for o in tr.tracks(t + 0.6)}
    assert all(o["state"] == "visible" for o in before.values())
    tr.drop_camera("camA", t + 0.61)
    after = {o["pos"][1] > 1: o for o in tr.tracks(t + 0.62)}
    sole, shared = after[False], after[True]
    assert sole["state"] == "predicted" and sole["id"] == before[False]["id"] and "camA" not in sole["seen_by"]
    assert shared["state"] == "visible" and shared["seen_by"] == ["camB"]
    jump = ((shared["pos"][0] - before[True]["pos"][0]) ** 2 + (shared["pos"][1] - before[True]["pos"][1]) ** 2) ** .5
    assert jump < 0.02, jump


def test_server_disconnect_purges_instantly():
    server.camera_config = {"camA": {"pos": [0, 0, 1], "yaw": 0, "fov": 60}, "camB": {"pos": [1, 0, 1]}}
    server.camera_state = {}
    server.camera_timing = {}
    server.tracker = Tracker()
    server.registry = CameraRegistry(["camA", "camB"], stale_timeout=2.5, log=lambda m: None)
    jpeg = server.offline_jpeg("x", "y")
    for i in range(12):
        server.process_message("camA", {"cam": "camA", "t": time.time(), "frame": i, "state": TRUSTED, "weight": 1.0,
                                        "detections": [det(0.5, 0.5)]}, jpeg)
    assert server.tracker.tracks(time.time())[0]["state"] == "visible"
    server.apply_transitions(server.registry.on_disconnect("camA", time.monotonic()))
    assert server.camera_state["camA"]["detections"] == []                            # purged
    assert server.camera_state["camA"]["jpeg"] == server.offline_jpeg("camA", OFFLINE)  # no frozen feed
    assert server.tracker.tracks(time.time())[0]["state"] == "predicted"               # sole-seen -> predicted
    status = {c["id"]: c for c in server.cameras_status()}
    assert status["camA"]["state"] == OFFLINE and status["camA"]["weight"] == 0.0
    # EXCLUDED: preview kept, detections ignored
    server.process_message("camA", {"cam": "camA", "t": time.time(), "frame": 99, "state": EXCLUDED, "weight": 0,
                                    "detections": [det(0.5, 0.5)]}, jpeg)
    assert server.camera_state["camA"]["detections"] == [] and server.camera_state["camA"]["jpeg"] == jpeg
    assert server.tracker.tracks(time.time())[0]["state"] == "predicted"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all camera/disconnect tests passed")
