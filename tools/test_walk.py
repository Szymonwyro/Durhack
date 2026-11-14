"""Recorded test walk -> replayable detection messages, so the Quest / map / server can be built without cameras.

    # 1. (Mac-1, once) record:  python -m calib.capture camA --record 60
    # 2. (Mac-1, once) detect + project to the floor -> samples/test_walk_camA.jsonl (small, in git):
    python -m tools.test_walk make recordings/camA_<stamp>.mp4
    # 3. (anyone) replay into a server's WS /ingest at the original pace, timestamps shifted to now:
    python3 tools/test_walk.py replay                                  # ws://127.0.0.1:8001/ingest, loops
    python3 tools/test_walk.py replay --url ws://127.0.0.1:8001/ingest --speed 2 --once
    python3 tools/test_walk.py replay --print                          # just print the messages, no server

Each line is a contract 'detection' message (CLAUDE.md / docs/CONTRACTS.md):
    {"cam":"camA","t":<unix>,"foot":[x,y],"bbox":[x,y,w,h],"conf":0.9}
plus "frame" and "dt" (seconds since the start of the recording). The first line is a {"type":"meta",...} header
(camera pose, area, source video) that replay skips. Frames with nobody in them appear as
{"cam":"camA","t":...,"frame":n,"dt":...,"empty":true} so gaps (person out of view) keep their timing; replay skips them too.
`replay` needs only Python 3 + the `websockets` package (pip install websockets).
"""
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "samples" / "test_walk_camA.jsonl"
AREA = (0.0, 0.0, 1.5, 1.5)  # corner markers 1/7/3/5
MARGIN = 0.75                # keep detections up to this far outside the marker square (drops people on sofas etc.)


def make(a):
    sys.path.insert(0, str(ROOT))
    from calib.config_io import get_camera
    from perception.detect import detect_video
    from perception.floor_project import project_detection

    video = Path(a.video)
    cam = get_camera(a.cam)
    calib = {k: cam[k] for k in ("id", "K", "dist", "R", "t")}
    stamps_file = video.with_name(video.stem + "_timestamps.csv")
    stamps = [float(r["unix_t"]) for r in csv.DictReader(stamps_file.open())] if stamps_file.exists() else None
    t0 = stamps[0] if stamps else time.time()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    n_det = n_frames = n_cut = n_out = 0
    with out.open("w") as f:
        f.write(json.dumps({"type": "meta", "cam": a.cam, "video": video.name, "recorded_t0": t0,
                            "camera": {k: cam.get(k) for k in ("pos", "yaw", "fov", "width", "height")},
                            "area": {"x": [AREA[0], AREA[2]], "y": [AREA[1], AREA[3]]},
                            "note": "foot = floor point in metres (world frame); t = recording time; replay shifts t"}) + "\n")
        for fr in detect_video(video, ROOT / "yolo11n.pt", confidence=a.conf, classes=[0], device=a.device):
            n_frames += 1
            i = fr["frame"]
            t = stamps[i] if stamps and i < len(stamps) else t0 + fr["time_seconds"]
            base = {"cam": a.cam, "t": round(t, 3), "frame": i, "dt": round(t - t0, 3)}
            kept = 0
            for d in fr["detections"]:
                x, y, w, h = d["bbox"]
                if y + h >= fr["height"] - 3:  # feet cut off by the image edge -> floor point would be wrong
                    n_cut += 1
                    continue
                p = project_detection(d, calib)["position"]
                if not (AREA[0] - MARGIN <= p[0] <= AREA[2] + MARGIN and AREA[1] - MARGIN <= p[1] <= AREA[3] + MARGIN):
                    n_out += 1
                    continue
                f.write(json.dumps({**base, "foot": [round(p[0], 3), round(p[1], 3)],
                                    "bbox": [round(v, 1) for v in d["bbox"]], "conf": round(d["confidence"], 2)}) + "\n")
                kept += 1
            if not kept:
                f.write(json.dumps({**base, "empty": True}) + "\n")
            n_det += kept
            if n_frames % 300 == 0:
                print(f"{n_frames} frames, {n_det} detections", flush=True)
    print(f"{n_frames} frames -> {n_det} detections in {out.relative_to(ROOT)}  "
          f"(dropped: {n_cut} feet cut off, {n_out} outside the area)")


def load(path):
    rows = [json.loads(line) for line in Path(path).open()]
    return [r for r in rows if r.get("type") != "meta"]


async def replay(a):
    rows = load(a.file)
    if not rows:
        sys.exit("no rows")
    dets = sum(1 for r in rows if "foot" in r)
    print(f"{len(rows)} frames, {dets} detections, {rows[-1]['dt']:.0f} s per loop, speed x{a.speed}", flush=True)
    ws = None
    if not a.print:
        import websockets
        ws = await websockets.connect(a.url)
        print("connected to", a.url, flush=True)
    loop = 0
    while True:
        start = time.time()
        for r in rows:
            wait = start + r["dt"] / a.speed - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
            if "foot" not in r:
                continue
            msg = {"cam": r["cam"], "t": time.time(), "foot": r["foot"], "bbox": r["bbox"], "conf": r["conf"]}
            if ws is None:
                print(json.dumps(msg), flush=True)
            else:
                await ws.send(json.dumps(msg))
        loop += 1
        print(f"loop {loop} done", flush=True)
        if a.once:
            break
        await asyncio.sleep(1.0)
    if ws is not None:
        await ws.close()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make", help="video -> detections JSONL (Mac-1, needs the venv)")
    m.add_argument("video")
    m.add_argument("--cam", default="camA")
    m.add_argument("--out", default=str(DEFAULT_OUT))
    m.add_argument("--conf", type=float, default=0.4)
    m.add_argument("--device", default=None, help="mps / cpu / cuda")
    r = sub.add_parser("replay", help="send the JSONL to a server's WS /ingest")
    r.add_argument("file", nargs="?", default=str(DEFAULT_OUT))
    r.add_argument("--url", default="ws://127.0.0.1:8001/ingest")
    r.add_argument("--speed", type=float, default=1.0)
    r.add_argument("--once", action="store_true", help="play once instead of looping")
    r.add_argument("--print", action="store_true", help="print messages instead of sending")
    a = p.parse_args()
    make(a) if a.cmd == "make" else asyncio.run(replay(a))


if __name__ == "__main__":
    main()
