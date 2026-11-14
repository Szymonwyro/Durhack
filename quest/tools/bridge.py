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
    python3 quest/tools/bridge.py --tls-port 8443 --cert quest/.certs/cert.pem --key quest/.certs/key.pem
                      # untethered: also HTTPS/WSS on 0.0.0.0:8443 for the Quest over Wi-Fi (same endpoints)
Only quest/ and config/server_url.js|server.json are served as static files; /ingest accepts this machine only.
"""
import argparse
import asyncio
import json
import math
import random
import ssl
import sys
import threading
import time
from pathlib import Path

from aiohttp import WSMsgType, web

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PREDICT_AFTER_S = 0.5   # 'predicted' when unseen for > 0.5 s
DROP_PREDICTED_S = 8.0  # INTERFACE.md: drop once predicted for > ~8 s ...
DROP_2SIGMA_M = 3.0     # ... or its 2-sigma radius exceeds ~3 m
WORLD_VERSION = 1
MAX_SPEED = 3.0         # m/s, cap on estimated walking speed
GATE_M = 1.0            # max distance from a seen track to accept a detection (+ 2 sigma while predicted)
MEAS_SIGMA = 0.10       # m, detection noise
GROWTH = 0.35           # m/s, sigma growth while predicted

# --sim world = the browser mock (quest/src/mock.js): wall A at x=2.1, blind spot / PIR zone at x<1, y>0.3
SIM_BLIND = [[-0.3, 0.3], [1.0, 0.3], [1.0, 2.0], [-0.3, 2.0]]
SIM_WORLD = {
    "walls": [{"a": [2.1, -0.6], "b": [2.1, 2.0], "h": 2.0}, {"a": [-0.4, 2.1], "b": [1.2, 2.1], "h": 2.0}],
    "cameras": [{"id": "camA", "pos": [-0.6, -0.6, 1.5], "yaw": 45, "fov": 70},
                {"id": "camB", "pos": [2.8, 0.8, 1.5], "yaw": 180, "fov": 70}],
    "zones": [{"sensor": "pir1", "zone": SIM_BLIND}],
    "objects": [{"id": "exit1", "type": "exit", "pos": [-0.6, 0.9], "size": [0.1, 0.9, 2.0], "label": "Fire exit"}],
}


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


def align_points():
    """The 4 corner markers from config/markers.json, in file order (= touch order round the area)."""
    markers = (load_json(ROOT / "config" / "markers.json") or {}).get("markers", [])
    return [{"id": f"m{m['id']}", "marker": m["id"], "pos": [m["x"], m["y"]]} for m in markers]


def build_world(sim):
    """World message per quest/INTERFACE.md section 2 (without "type"; /tracks wraps it)."""
    if sim:
        world = dict(SIM_WORLD)
    else:
        walls_cfg = load_json(ROOT / "config" / "walls.json") or {}
        cams = [camera_pose(c) for c in (load_json(ROOT / "config" / "cameras.json") or {}).get("cameras", [])
                if all(k in c for k in ("R", "t"))]
        world = {"walls": walls_cfg.get("walls", []), "cameras": cams, "zones": [], "objects": []}
    return {"v": 1, "world_version": WORLD_VERSION, **world, "align_points": align_points()}


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
            dtv = max(dt_meas, 0.05)  # two detections in the same frame must not explode vel
            self.v = [self.v[0] + beta * r[0] / dtv, self.v[1] + beta * r[1] / dtv]
            speed = math.hypot(*self.v)
            if speed > MAX_SPEED:
                self.v = [self.v[0] * MAX_SPEED / speed, self.v[1] * MAX_SPEED / speed]
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
        self.pir = {}  # sensor -> (active, t)
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

    def zone(self, sensor, t, zone, active):
        """PIR state for the tracks message; an active zone pulls nearby predicted tracks to its centroid."""
        with self.lock:
            self.pir[sensor] = (bool(active), t)
        if not active or not zone:
            return
        c = [sum(p[0] for p in zone) / len(zone), sum(p[1] for p in zone) / len(zone)]
        with self.lock:
            for tr in self.tracks:
                if t - tr.last_seen > PREDICT_AFTER_S and math.dist(tr.x, c) < 2.5:
                    tr.x = [tr.x[0] + 0.2 * (c[0] - tr.x[0]), tr.x[1] + 0.2 * (c[1] - tr.x[1])]

    def snapshot(self, now):
        with self.lock:
            self.tracks = [tr for tr in self.tracks
                           if now - tr.last_seen - PREDICT_AFTER_S <= DROP_PREDICTED_S
                           and 2 * math.sqrt(tr.sigma2(now)) <= DROP_2SIGMA_M]
            for tr in self.tracks:
                tr.predict(now)
            return {"type": "tracks", "v": 1, "t": round(now, 3), "world_version": WORLD_VERSION,
                    "tracks": [tr.msg(now) for tr in self.tracks],
                    "zones": [{"sensor": s, "active": a, "t": round(t, 3)} for s, (a, t) in self.pir.items()]}


# ---------------------------------------------------------------- detection sources
async def sim_camera(tracker):
    """Same loop as the browser mock: behind wall A (camB still sees them), through the blind spot
    (no detections, PIR pir1 active), 2 laps then a 10 s gap with nobody there."""
    c, rx, ry, lap, gap = (1.4, 0.8), 1.3, 1.0, 9.0, 10.0
    in_blind = lambda x, y: -0.3 <= x <= 1.0 and 0.3 <= y <= 2.0
    t0 = time.time()
    while True:
        t = time.time()
        ph = (t - t0) % (2 * lap + gap)
        blind = False
        if ph < 2 * lap:
            a = 2 * math.pi * ph / lap
            x, y = c[0] + rx * math.cos(a), c[1] + ry * math.sin(a)
            blind = in_blind(x, y)
            if not blind:
                cam = "camB" if x > 2.1 else "camA"
                tracker.detection(cam, t, [x + random.gauss(0, 0.03), y + random.gauss(0, 0.03)])
        tracker.zone("pir1", t, SIM_BLIND, blind)
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
        await ws.send_json({"type": "world", **world})
        await ws.send_json(tracker.snapshot(time.time()))  # current snapshot immediately
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
        if request.remote not in ("127.0.0.1", "::1"):   # LAN-facing TLS port: no fake people from the venue Wi-Fi
            raise web.HTTPForbidden(text="ingest is local only")
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
                    tracker.zone(d.get("sensor", "pir"), float(d.get("t", time.time())), d["zone"], d.get("active", False))
            except (ValueError, KeyError, TypeError) as e:
                print("[bridge] bad ingest message:", e, m.data[:200], flush=True)
        return ws

    log_path = ROOT / "logs" / "quest.log"

    async def post_log(request):
        """Quest remote debug (?debug=1): append entries as JSON lines to logs/quest.log; echo errors here."""
        try:
            body = await request.json()
            entries = body.get("entries", [])
            assert isinstance(entries, list)
        except (ValueError, AssertionError, AttributeError):
            raise web.HTTPBadRequest(text="expected {client, entries:[...]}")
        log_path.parent.mkdir(exist_ok=True)
        with log_path.open("a") as f:
            for e in entries[:500]:
                via = f"{request.scheme}://{request.host} from {request.remote}"  # which route the Quest is on
                f.write(json.dumps({"client": body.get("client", "?"), "via": via, **e}) + "\n")
                if isinstance(e, dict) and e.get("level") in ("error", "warn"):
                    print(f"[quest {e['level']}] {str(e.get('msg'))[:200]}", flush=True)
        return web.json_response({"ok": True, "n": len(entries)})

    async def static(request):
        rel = request.match_info["path"]
        path = (ROOT / rel).resolve()
        allowed = (ROOT / "quest") in path.parents or path == ROOT / "quest" \
            or path in (ROOT / "config" / "server_url.js", ROOT / "config" / "server.json")
        if not allowed or ".certs" in path.parts:
            raise web.HTTPNotFound()
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
    app.router.add_post("/log", post_log)
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
    ap.add_argument("--tls-port", type=int, default=0, help="also serve HTTPS/WSS on this port (e.g. 8443)")
    ap.add_argument("--tls-host", default="0.0.0.0")
    ap.add_argument("--cert", default=str(ROOT / "quest" / ".certs" / "cert.pem"))
    ap.add_argument("--key", default=str(ROOT / "quest" / ".certs" / "key.pem"))
    args = ap.parse_args()

    async def serve():
        runner = web.AppRunner(make_app(args))
        await runner.setup()
        await web.TCPSite(runner, args.host, args.port).start()
        print(f"[bridge] http://{args.host}:{args.port}/quest/  sim={args.sim} cameras={args.camera or []}", flush=True)
        if args.tls_port:
            ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ctx.load_cert_chain(args.cert, args.key)
            await web.TCPSite(runner, args.tls_host, args.tls_port, ssl_context=ctx).start()
            print(f"[bridge] https://{args.tls_host}:{args.tls_port}/quest/  (TLS)", flush=True)
        await asyncio.Event().wait()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
