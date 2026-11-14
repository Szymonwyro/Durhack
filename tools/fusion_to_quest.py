"""Live camera data -> Quest: forward the fusion server's fused positions into Role 1's bridge, which turns them
into the exact quest/INTERFACE.md format (stable ids, Kalman vel/cov, visible/predicted, world + align_points).

    fusion server (laptop 2, :8000)  --WS /tracks-->  this  --WS /ingest-->  quest/tools/bridge.py (:8001)  -->  Quest

Run on the machine the Quest is plugged into, next to the bridge:
    python3 quest/tools/bridge.py                                           # terminal 1 (Role 1's stand-in server)
    python3 tools/fusion_to_quest.py ws://10.247.206.87:8000/tracks        # terminal 2
    ./quest/dev.sh live                                                    # or: Role 1's dev loop with the bridge
Options: --bridge ws://127.0.0.1:8001/ingest (default), --margin 0.75 (m around the marker square), --print (show what would be sent, no bridge needed).

Only `person` tracks are forwarded, and each position only once (the fusion server repeats a camera's last frame
for up to 1.5 s, and repeats would freeze the Kalman velocity). Timestamps are this machine's receive time,
because laptop clocks differ. Needs only Python 3 + `pip install websockets` (the bridge itself needs `pip install aiohttp`).
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

MARKERS = Path(__file__).resolve().parent.parent / "config" / "markers.json"


async def run(a):
    import websockets

    xs, ys = zip(*[(m["x"], m["y"]) for m in json.loads(MARKERS.read_text())["markers"]])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    print(f"forwarding people within {a.margin} m of the marker area x {x0}..{x1}, y {y0}..{y1}", flush=True)
    bridge = None
    last = set()  # (cams, pos) in the previous fusion message
    n_in = n_out = 0
    t_report = time.time()
    while True:
        try:
            if not a.print and bridge is None:
                bridge = await websockets.connect(a.bridge)
                print("connected to bridge", a.bridge, flush=True)
            async with websockets.connect(a.fusion, max_size=4_000_000) as src:
                print("connected to fusion server", a.fusion, flush=True)
                async for raw in src:
                    msg = json.loads(raw)
                    now = time.time()
                    n_in += 1
                    seen = set()
                    for tr in msg.get("tracks", []):
                        if tr.get("class_name", "person") != "person" or not tr.get("pos"):
                            continue
                        pos = (round(float(tr["pos"][0]), 3), round(float(tr["pos"][1]), 3))
                        if not (x0 - a.margin <= pos[0] <= x1 + a.margin and y0 - a.margin <= pos[1] <= y1 + a.margin):
                            continue  # someone outside the demo area (sofas, passers-by)
                        cams = "+".join(sorted(tr.get("seen_by") or ["fusion"]))
                        seen.add((cams, pos))
                        if (cams, pos) in last:  # same camera frame repeated by the server
                            continue
                        det = {"cam": cams, "t": now, "foot": list(pos)}
                        n_out += 1
                        if a.print:
                            print(json.dumps(det), flush=True)
                        else:
                            await bridge.send(json.dumps(det))
                    last = seen
                    if now - t_report > 5:
                        print(f"{n_in} fusion messages in, {n_out} person positions forwarded (last 5 s)", flush=True)
                        n_in = n_out = 0
                        t_report = now
        except (OSError, websockets.WebSocketException) as e:
            print(f"connection problem ({e}); retrying in 2 s", flush=True)
            if bridge is not None:
                await bridge.close()
                bridge = None
            await asyncio.sleep(2)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("fusion", help="fusion server tracks URL, e.g. ws://10.247.206.87:8000/tracks")
    p.add_argument("--bridge", default="ws://127.0.0.1:8001/ingest", help="Role 1 bridge ingest URL")
    p.add_argument("--margin", type=float, default=0.75, help="metres outside the marker square still forwarded")
    p.add_argument("--print", action="store_true", help="print instead of sending to the bridge")
    try:
        asyncio.run(run(p.parse_args()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
