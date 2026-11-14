"""Pre-push smoke test: boxes (no --pose) still work end to end.   python tests/smoke_boxes.py [--video FILE]

1. starts fusion.server on a spare port
2. replays samples/test_walk2_camA.jsonl into /ingest/camA (worker format) and checks /tracks: a person track with
   id/state/pos/vel/cov, and NO skeleton (boxes only)
3. runs the real camera worker WITHOUT --pose on a video (default: newest recordings/camA_*.mp4, else camera 1) and
   checks the server sees camA TRUSTED/DEGRADED with detections that carry no kp3d
Exit code 0 = pass.
"""
import argparse
import asyncio
import glob
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8013
PY = sys.executable


def get(path):
    return json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=3).read())


async def watch_tracks(seconds):
    import websockets

    people, skeletons, ids = 0, 0, set()
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/tracks") as ws:
        end = time.time() + seconds
        while time.time() < end:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 5))
            for t in msg["tracks"]:
                if t.get("class_name") == "person":
                    assert all(k in t for k in ("id", "state", "pos", "vel", "cov", "last_seen", "seen_by")), t
                    people += 1
                    ids.add(t["id"])
                    skeletons += "skeleton" in t
    return people, skeletons, ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    a = ap.parse_args()
    videos = sorted(v for v in glob.glob(str(ROOT / "recordings" / "camA_*.mp4")) if "skeleton" not in v)
    source = a.video or (videos[-1] if videos else "1")
    procs = []
    try:
        procs.append(subprocess.Popen([PY, "-m", "uvicorn", "fusion.server:app", "--port", str(PORT)], cwd=ROOT,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        for _ in range(40):
            try:
                get("/health")
                break
            except OSError:
                time.sleep(0.25)
        # 2. replay (boxes)
        replay = subprocess.Popen([PY, "tools/test_walk.py", "replay", "samples/test_walk2_camA.jsonl", "--fusion",
                                   "--url", f"ws://127.0.0.1:{PORT}/ingest/camA", "--once", "--speed", "3"], cwd=ROOT,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(replay)
        people, skeletons, ids = asyncio.run(watch_tracks(8))
        assert people > 20, f"replay: only {people} person track entries"
        assert skeletons == 0, "replay: boxes-only sample produced skeletons"
        print(f"ok replay: {people} person entries, ids {sorted(ids)}, no skeletons")
        replay.wait(timeout=15)
        time.sleep(3)  # let camA go OFFLINE (stale) before the worker connects
        # 3. real worker, no --pose
        worker = subprocess.Popen([PY, "-m", "tools.camera_worker", "camA", "--source", source, "--server",
                                   f"ws://127.0.0.1:{PORT}/ingest/camA", "--classes", "0"], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        procs.append(worker)
        ok = False
        for _ in range(60):
            time.sleep(0.5)
            cam = next(c for c in get("/cameras")["cameras"] if c["id"] == "camA")
            if cam["state"] in ("TRUSTED", "DEGRADED") and cam.get("fps"):
                ok = True
                break
        assert ok, f"worker: camA never became active ({cam})"
        people, skeletons, ids = asyncio.run(watch_tracks(5))
        assert skeletons == 0, "worker without --pose produced skeletons"
        print(f"ok worker (source {Path(str(source)).name}): camA {cam['state']} {cam['fps']} fps, "
              f"{people} person entries, no skeletons")
        print("SMOKE PASS: boxes work")
        return 0
    except AssertionError as e:
        print("SMOKE FAIL:", e)
        return 1
    finally:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    sys.exit(main())
