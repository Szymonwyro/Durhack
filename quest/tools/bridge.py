"""Quest dev bridge: a small stand-in for fusion/server.py (Role 2) until that exists.

Serves, on one port (default 127.0.0.1:8001, which the Quest reaches over `adb reverse`):
  - the repo's static files (no-cache), so http://localhost:8001/quest/ works
  - GET /world, WS /tracks (world on connect, then tracks at ~15 Hz), WS /ingest (detection, zone)
Messages follow CLAUDE.md / docs/CONTRACTS.md exactly.

Detections come from any of:
  --sim             a simulated camA: one person walking a loop that goes behind the x=1 wall
  --camera camA     run YOLO on a calibrated camera from config/cameras.json (needs K, dist, R, t,
                    plus cv2 + ultralytics in this Python), using perception/ detect + floor_project
  WS /ingest        camera workers sending contract 'detection' / 'zone' messages

    python3 quest/tools/bridge.py --sim
    python3 quest/tools/bridge.py --camera camA
    python3 quest/tools/bridge.py --host 0.0.0.0     # let workers on other machines reach /ingest
"""
import argparse
import asyncio
import json
import math
import random
import sys
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PREDICT_AFTER_S = 0.5   # contract: 'predicted' when unseen for > 0.5 s
DROP_AFTER_S = 10.0     # contract: dropped after 10 s unseen
GATE_M = 1.0            # max distance from a seen track to accept a detection (+ 2 sigma while predicted)
MEAS_SIGMA = 0.10       # m, detection noise
GROWTH = 0.35           # m/s, sigma growth while predicted

SIM_WORLD_WALLS = [{"a": [1.0, 0.0], "b": [1.0, 1.5], "h": 2.0}, {"a": [2.0, 2.0], "b": [3.0, 2.0], "h": 2.0}]
SIM_CAMERA = {"id": "camA", "pos": [-0.5, -0.5, 1.5], "yaw": 45.0, "fov": 70.0}


# ---------------------------------------------------------------- world
def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def camera_pose(cam):
    """World pose from extrinsics X_cam = R X_world + t -> pos, yaw (deg, from +x CCW)."""
    R, t = cam["R"], cam["t"]
    t = [float(v[0] if isinstance(v, list) else v) for v in t]  # [x,y,z] or [[x],[y],[z]]
    # position = -R^T t
    pos = [-(R[0][i] * t[0] + R[1][i] * t[1] + R[2][i] * t[2]) for i in range(3)]
    fwd = [R[2][0], R[2][1]]  # optical axis (camera +z) in world = 3rd row of R
    yaw = math.degrees(math.atan2(fwd[1], fwd[0]))
    return {"id": cam["id"], "pos": [round(v, 3) for v in pos], "yaw": round(yaw, 1), "fov": cam.get("fov", 70.0)}


def build_world(sim):
    walls_cfg = load_json(ROOT / "config" / "walls.json") or load_json(ROOT / "config" / "walls.example.json") or {}
    walls = walls_cfg.get("walls", [])
    cams = []
    for c in (load_json(ROOT / "config" / "cameras.json") or {}).get("cameras", []):
        if all(k in c for k in ("R", "t")):
            cams.append(camera_pose(c))
    if sim:
        walls = SIM_WORLD_WALLS
        if not any(c["id"] == "camA" for c in cams):
            cams.append(SIM_CAMERA)
    return {"walls": walls, "cameras": cams, "zones": []}


# ---------------------------------------------------------------- tracker
class Track:
    _next = 1

    def __init__(self, xy, t, cam):
        self.id = Track._next
        Track._next += 1
        self.x = list(xy)
        self.v = [0.0, 0.0]
        self.t = t               # time of last predict/update
        self.last_seen = t
        self.seen_by = {cam: t}

    def predict(self, t):
        dt = t - self.t
        if dt <= 0:
            return
        unseen = t - self.last_seen
        if unseen > PREDICT_AFTER_S:      # coast with slowly decaying velocity
            k = math.exp(-dt / 1.5)
            self.v = [self.v[0] * k, self.v[1] * k]
        self.x = [self.x[0] + self.v[0] * dt, self.x[1] + self.v[1] * dt]
        self.t = t

    def update(self, z, t, cam):
        dt_meas = max(t - self.last_seen, 1e-3)
        self.predict(t)
        if dt_meas > PREDICT_AFTER_S:   # re-acquired after prediction: trust the camera, restart velocity
            self.x, self.v = list(z), [0.0, 0.0]
            self.last_seen = t
            self.seen_by[cam] = t
            return
        alpha, beta = 0.5, 0.15
        r = [z[0] - self.x[0], z[1] - self.x[1]]
        self.x = [self.x[0] + alpha * r[0], self.x[1] + alpha * r[1]]
        if dt_meas < 1.0:
            self.v = [self.v[0] + beta * r[0] / dt_meas, self.v[1] + beta * r[1] / dt_meas]
        self.last_seen = t
        self.seen_by[cam] = t

    def sigma2(self, now):
        unseen = max(now - self.last_seen, 0.0)
        return MEAS_SIGMA ** 2 + (GROWTH * max(unseen - PREDICT_AFTER_S, 0.0)) ** 2

    def msg(self, now):
        unseen = max(now - self.last_seen, 0.0)
        predicted = unseen > PREDICT_AFTER_S
        s2 = self.sigma2(now)
        return {
            "id": self.id,
            "state": "predicted" if predicted else "visible",
            "pos": [round(self.x[0], 3), round(self.x[1], 3)],
            "vel": [round(self.v[0], 3), round(self.v[1], 3)],
            "cov": [[round(s2, 4), 0.0], [0.0, round(s2, 4)]],
            "last_seen": round(self.last_seen, 3),
            "seen_by": sorted(c for c, ts in self.seen_by.items() if now - ts <= PREDICT_AFTER_S),
        }


class Tracker:
    def __init__(self):
        self.tracks = []
        self.lock = threading.Lock()

    def detection(self, cam, t, foot):
        with self.lock:
            best, best_score = None, 1.0
            for tr in self.tracks:
                tr.predict(t)
                gate = GATE_M + 2 * math.sqrt(tr.sigma2(t))
                score = math.dist(tr.x, foot) / gate  # < 1 = inside this track's gate
                if score < best_score:
                    best, best_score = tr, score
            if best:
                best.update(foot, t, cam)
            else:
                self.tracks.append(Track(foot, t, cam))

    def zone(self, t, zone, active):
        """Active PIR zone: pull nearby predicted tracks towards its centroid (loose measurement)."""
        if not active or not zone:
            return
        c = [sum(p[0] for p in zone) / len(zone), sum(p[1] for p in zone) / len(zone)]
        with self.lock:
            for tr in self.tracks:
                if t - tr.last_seen > PREDICT_AFTER_S and math.dist(tr.x, c) < 2.5:
                    tr.x = [tr.x[0] + 0.2 * (c[0] - tr.x[0]), tr.x[1] + 0.2 * (c[1] - tr.x[1])]

    def snapshot(self, now):
        with self.lock:
            self.tracks = [tr for tr in self.tracks if now - tr.last_seen <= DROP_AFTER_S]
            for tr in self.tracks:
                tr.predict(now)
            return {"t": round(now, 3), "tracks": [tr.msg(now) for tr in self.tracks]}


# ---------------------------------------------------------------- detection sources
async def sim_camera(tracker):
    """camA sees one person on an ellipse, except while they are behind the x=1 wall (x > 1.1)."""
    c, rx, ry, period = (1.0, 0.75), 0.8, 0.5, 6.5
    t0 = time.time()
    while True:
        t = time.time()
        a = 2 * math.pi * (t - t0) / period
        x, y = c[0] + rx * math.cos(a), c[1] + ry * math.sin(a)
        if x <= 1.1:
            tracker.detection("camA", t, [x + random.gauss(0, 0.03), y + random.gauss(0, 0.03)])
        await asyncio.sleep(0.1)  # ~10 fps camera


def real_camera(tracker, cam_id, model, stop):
    """Run perception/ on a calibrated camera in a thread, feeding person foot points to the tracker."""
    from calib.config_io import parse_source
    from perception.detect import detect_video
    from perception.floor_project import project_detection

    cams = {c["id"]: c for c in (load_json(ROOT / "config" / "cameras.json") or {}).get("cameras", [])}
    cam = cams.get(cam_id)
    missing = [k for k in ("source", "K", "dist", "R", "t") if not cam or k not in cam]
    if missing:
        print(f"[bridge] camera {cam_id!r} is missing {missing} in config/cameras.json "
              "(run calib/intrinsics.py then calib/extrinsics.py)", flush=True)
        return
    for frame in detect_video(parse_source(cam["source"]), model_path=model, classes=[0],
                              on_frame=lambda img, res: not stop.is_set()):
        t = time.time()
        for det in frame["detections"]:
            try:
                wd = project_detection(det, cam)
            except ValueError:
                continue
            tracker.detection(cam_id, t, wd["position"])


# ---------------------------------------------------------------- web
def make_app(args):
    tracker = Tracker()
    world = build_world(args.sim)
    clients = set()

    @web.middleware
    async def no_cache(request, handler):
        resp = await handler(request)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    async def get_world(request):
        return web.json_response(world)

    async def ws_tracks(request):
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        await ws.send_json(world)
        clients.add(ws)
        print(f"[bridge] /tracks client connected ({len(clients)})", flush=True)
        try:
            async for _ in ws:
                pass
        finally:
            clients.discard(ws)
            print(f"[bridge] /tracks client left ({len(clients)})", flush=True)
        return ws

    async def ws_ingest(request):
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        async for m in ws:
            if m.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(m.data)
                if "foot" in d:
                    tracker.detection(d["cam"], float(d.get("t", time.time())), d["foot"])
                elif "zone" in d:
                    tracker.zone(float(d.get("t", time.time())), d["zone"], d.get("active", False))
            except (ValueError, KeyError, TypeError) as e:
                print("[bridge] bad ingest message:", e, m.data[:200], flush=True)
        return ws

    async def static(request):
        rel = request.match_info["path"]
        path = (ROOT / rel).resolve()
        if ROOT not in path.parents and path != ROOT:
            raise web.HTTPForbidden()
        if path.is_dir():
            if not request.path.endswith("/"):
                raise web.HTTPFound(request.path + "/" + (("?" + request.query_string) if request.query_string else ""))
            path = path / "index.html"
        if not path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    async def broadcast(app):
        while True:
            msg = json.dumps(tracker.snapshot(time.time()))
            for ws in list(clients):
                try:
                    await ws.send_str(msg)
                except ConnectionError:
                    clients.discard(ws)
            await asyncio.sleep(1 / 15)

    stop = threading.Event()

    async def on_startup(app):
        app["tasks"] = [asyncio.create_task(broadcast(app))]
        if args.sim:
            app["tasks"].append(asyncio.create_task(sim_camera(tracker)))
        for cam_id in args.camera or []:
            threading.Thread(target=real_camera, args=(tracker, cam_id, args.model, stop), daemon=True).start()

    async def on_cleanup(app):
        stop.set()
        for task in app["tasks"]:
            task.cancel()

    app = web.Application(middlewares=[no_cache])
    app.router.add_get("/world", get_world)
    app.router.add_get("/tracks", ws_tracks)
    app.router.add_get("/ingest", ws_ingest)
    app.router.add_get("/{path:.*}", static)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--sim", action="store_true", help="simulated camA person walking behind a wall")
    ap.add_argument("--camera", action="append", help="calibrated camera id from config/cameras.json (repeatable)")
    ap.add_argument("--model", default=str(ROOT / "yolo11n.pt"), help="YOLO weights")
    args = ap.parse_args()
    print(f"[bridge] http://{args.host}:{args.port}/quest/  sim={args.sim} cameras={args.camera or []}", flush=True)
    web.run_app(make_app(args), host=args.host, port=args.port, print=None)
