"""LAN fusion server for camera detections, previews, and the shared map."""

import asyncio
import base64
import binascii
import json
import math
import time
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from calib.config_io import CAMERAS, MARKERS, ROOT
from calib.refresh import load_thresholds
from fusion.cameras import EXCLUDED, OFFLINE, CameraRegistry
from fusion.tracker import Tracker
from perception.floor_project import match_floor_detections

WALLS = ROOT / "config" / "walls.json"
MAP_PAGE = ROOT / "web" / "map" / "index.html"
MAX_MESSAGE_BYTES = 2_000_000
CAMERA_STALE_SECONDS = 1.5
MATCH_DISTANCE_METRES = 0.75

app = FastAPI(title="DurHack camera fusion")
camera_config: dict[str, dict[str, Any]] = {}
camera_state: dict[str, dict[str, Any]] = {}
state_lock = asyncio.Lock()
tracker = Tracker()  # people: stable ids, Kalman, visible/predicted, skeletons (fusion/tracker.py)
camera_timing: dict[str, Any] = {}  # camera -> CameraClock
THRESHOLDS = load_thresholds()       # config/refresh.json (stale_timeout etc.)
registry = CameraRegistry([], stale_timeout=THRESHOLDS["stale_timeout"])
_offline_jpegs: dict[str, bytes] = {}


def offline_jpeg(camera_id: str, state: str) -> bytes:
    """Grey placeholder for the feed, so a disconnected camera never shows a frozen last frame."""
    key = f"{camera_id}:{state}"
    if key not in _offline_jpegs:
        import cv2
        import numpy as np

        img = np.full((360, 640, 3), 70, np.uint8)
        cv2.putText(img, f"{camera_id} {state}", (40, 190), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (220, 220, 220), 3)
        _offline_jpegs[key] = cv2.imencode(".jpg", img)[1].tobytes()
    return _offline_jpegs[key]


def apply_transitions(changes: list[tuple[str, str, str]]) -> None:
    """Call with state_lock held. OFFLINE/EXCLUDED: purge the camera's detections, take it out of every track's
    seen_by and skeleton selection (tracks only it saw go predicted). OFFLINE also replaces its feed."""
    now = time.time()
    for camera_id, _old, new in changes:
        if new not in (OFFLINE, EXCLUDED):
            continue
        tracker.drop_camera(camera_id, now)
        state = camera_state.get(camera_id)
        if state is not None:
            state["detections"] = []
            if new == OFFLINE:
                state["jpeg"] = offline_jpeg(camera_id, OFFLINE)
                state["frame"] = state["frame"] + 1 if state.get("frame", -1) >= 0 else 0


def process_message(camera_id: str, message: dict[str, Any], jpeg: bytes | None) -> list:
    """Call with state_lock held, after validation. Updates the registry and, if the camera is active, the
    tracker. Returns the state transitions."""
    changes = registry.on_message(camera_id, message, time.monotonic())
    apply_transitions(changes)
    if message.get("type") == "status":
        return changes
    detections = message.get("detections") or []
    arrival = time.time()
    clock = camera_timing.setdefault(camera_id, CameraClock())
    if finite_number(message.get("t_server")):  # worker already converted to our clock
        captured = clock.capture_time(float(message["t_server"]), arrival, synced=True)
    else:
        captured = clock.capture_time(float(message["t"]), arrival)
    if registry.active(camera_id):
        tracker.camera_frame(camera_id, detections, arrival, t_capture=captured)
    else:
        detections = []  # EXCLUDED: preview only
    camera_state[camera_id] = {
        "received_at": time.monotonic(),
        "timestamp": float(message["t"]),
        "frame": message.get("frame", 0),
        "detections": detections,
        "jpeg": jpeg if jpeg is not None else offline_jpeg(camera_id, str(message.get("state"))),
    }
    return changes


def cameras_status() -> list[dict[str, Any]]:
    """Per camera: state, weight, fps, lag, pose (for GET /cameras, /api/world and /tracks)."""
    snap = registry.snapshot(time.monotonic())
    out = []
    for camera_id, camera in camera_config.items():
        reg = snap.get(camera_id, {"state": OFFLINE, "weight": 0.0, "reasons": []})
        timing = camera_timing[camera_id].stats() if camera_id in camera_timing else {}
        out.append({"id": camera_id, "pos": camera.get("pos", [0, 0, 0]), "yaw": camera.get("yaw", 0),
                    "fov": camera.get("fov", 70), **reg, "fps": timing.get("fps"), "lag_ms": timing.get("lag_ms")})
    return out


async def stale_watchdog() -> None:
    """Server-side hard disconnect: a camera that sends nothing for stale_timeout goes OFFLINE, whatever it
    last claimed (hung worker, dead Wi-Fi)."""
    while True:
        await asyncio.sleep(0.25)
        async with state_lock:
            apply_transitions(registry.check_stale(time.monotonic()))


class CameraClock:
    """Maps a worker's capture timestamps onto this server's clock and measures its delay.

    Workers send "t" = capture time on THEIR clock. If that clock agrees with ours (|median gap| < 1 s, i.e. both
    NTP-synced), capture time = t. Otherwise we only know the gap up to the latency, so we assume the fastest
    recent frame had ~0 delay: capture time = t + min(recent gaps). Lag = arrival - capture time.
    """

    def __init__(self) -> None:
        from collections import deque

        self.gaps = deque(maxlen=150)   # arrival - t (s)
        self.arrivals = deque(maxlen=60)
        self.lags = deque(maxlen=60)

    def capture_time(self, t_worker: float, arrival: float, synced: bool | None = None) -> float:
        self.gaps.append(arrival - t_worker)
        self.arrivals.append(arrival)
        gaps = sorted(self.gaps)
        median = gaps[len(gaps) // 2]
        self.synced = abs(median) < 1.0 if synced is None else synced
        captured = t_worker if self.synced else t_worker + gaps[0]
        captured = min(captured, arrival)
        self.lags.append(arrival - captured)
        return captured

    def stats(self) -> dict[str, Any]:
        lags = sorted(self.lags)
        span = self.arrivals[-1] - self.arrivals[0] if len(self.arrivals) > 1 else 0
        return {"fps": round((len(self.arrivals) - 1) / span, 1) if span > 0 else None,
                "lag_ms": round(lags[len(lags) // 2] * 1000) if lags else None,
                "clock": "synced" if getattr(self, "synced", True) else "offset (lag is relative to its fastest frame)"}

    # note: workers that send "t_server" (tools/camera_worker.py) are always "synced"


def load_world_config() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    data = json.loads(CAMERAS.read_text(encoding="utf-8"))
    cameras: dict[str, dict[str, Any]] = {}
    for camera in data.get("cameras", []):
        camera_id = camera.get("id")
        if not camera_id:
            raise ValueError("Every camera in config/cameras.json needs an id")
        missing = [key for key in ("K", "dist", "R", "t") if key not in camera]
        if missing:
            raise ValueError(
                f"Camera {camera_id!r} is missing calibration fields: {', '.join(missing)}"
            )
        cameras[camera_id] = camera
    if not cameras:
        raise ValueError("No calibrated cameras found in config/cameras.json")

    marker_data = json.loads(MARKERS.read_text(encoding="utf-8"))
    wall_data = json.loads(WALLS.read_text(encoding="utf-8")) if WALLS.is_file() else {"walls": []}
    return cameras, wall_data.get("walls", []), marker_data


def finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_detection(detection: Any, camera_id: str) -> bool:
    if not isinstance(detection, dict):
        return False
    position = detection.get("position")
    return (
        detection.get("camera_id") == camera_id
        and isinstance(detection.get("class_id"), int)
        and isinstance(detection.get("class_name"), str)
        and isinstance(position, list)
        and len(position) == 2
        and all(finite_number(value) for value in position)
        and finite_number(detection.get("confidence"))
        and 0 <= detection["confidence"] <= 1
    )


def map_tracks(snapshot: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    now = time.monotonic()
    observations: dict[str, list[dict[str, Any]]] = {}
    for camera_id, state in snapshot.items():
        if now - state["received_at"] <= CAMERA_STALE_SECONDS:
            observations[camera_id] = state["detections"]

    if not observations:
        return []

    # people come from the tracker (see tracks()); this only groups other classes (chairs, bags...) for the map
    observations = {cam: [d for d in dets if d.get("class_name") != "person"] for cam, dets in observations.items()}
    matched = match_floor_detections(observations, max_distance_m=MATCH_DISTANCE_METRES)
    tracks = []
    for track_id, detection in enumerate(matched, start=100000):
        tracks.append(
            {
                "id": track_id,
                "state": "visible",
                "class_id": detection["class_id"],
                "class_name": detection["class_name"],
                "pos": detection["position"],
                "seen_by": detection["cameras"],
                "conf": detection["confidence_by_camera"],
            }
        )
    return tracks


@app.on_event("startup")
async def startup() -> None:
    global camera_config, camera_state, registry
    camera_config, _, _ = load_world_config()
    camera_state = {}
    registry = CameraRegistry(list(camera_config), stale_timeout=THRESHOLDS["stale_timeout"])
    asyncio.get_running_loop().create_task(stale_watchdog())


@app.get("/")
@app.get("/map")
async def map_page() -> FileResponse:
    return FileResponse(MAP_PAGE)


@app.get("/health")
async def health() -> dict[str, Any]:
    async with state_lock:
        now = time.monotonic()
        return {
            "ok": True,
            "cameras": {
                camera_id: {
                    "online": camera_id in camera_state
                    and now - camera_state[camera_id]["received_at"] <= CAMERA_STALE_SECONDS,
                    "age_seconds": round(now - camera_state[camera_id]["received_at"], 2)
                    if camera_id in camera_state
                    else None,
                    **(camera_timing[camera_id].stats() if camera_id in camera_timing else {}),
                    "state": registry.cams.get(camera_id, {}).get("state", OFFLINE),
                    "weight": registry.cams.get(camera_id, {}).get("weight", 0.0),
                }
                for camera_id in camera_config
            },
        }


@app.get("/cameras")
async def cameras() -> dict[str, Any]:
    async with state_lock:
        return {"t": time.time(), "cameras": cameras_status()}


@app.get("/api/world")
async def world() -> JSONResponse:
    _, walls, marker_data = load_world_config()
    markers = marker_data["markers"]
    xs = [float(marker["x"]) for marker in markers]
    ys = [float(marker["y"]) for marker in markers]
    padding = 0.5
    return JSONResponse(
        {
            "cameras": cameras_status(),  # id, pos, yaw, fov + state, weight, fps, lag_ms
            "walls": walls,
            "bounds": [
                min(xs) - padding,
                max(xs) + padding,
                min(ys) - padding,
                max(ys) + padding,
            ],
        }
    )


@app.websocket("/ingest/{camera_id}")
async def ingest(websocket: WebSocket, camera_id: str) -> None:
    if camera_id not in camera_config:
        await websocket.close(code=1008, reason=f"Unknown camera {camera_id!r}")
        return
    await websocket.accept()
    try:
        while True:
            text = await websocket.receive_text()
            if len(text.encode("utf-8")) > MAX_MESSAGE_BYTES:
                await websocket.close(code=1009, reason="Message too large")
                return
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                await websocket.send_json({"error": "message must be valid JSON"})
                continue

            if not isinstance(message, dict) or not finite_number(message.get("t")):
                await websocket.send_json({"error": "message needs a numeric timestamp"})
                continue
            if message.get("cam") != camera_id:
                await websocket.send_json({"error": "payload camera ID must match ingest URL"})
                continue
            if message.get("type") == "status":  # e.g. the worker's final {"state": "OFFLINE"}
                async with state_lock:
                    process_message(camera_id, message, None)
                await websocket.send_json({"ok": True, "server_t": time.time()})
                continue
            detections = message.get("detections")
            jpeg_data = message.get("jpeg")
            frame_number = message.get("frame")
            if not isinstance(detections, list) or not all(
                validate_detection(detection, camera_id) for detection in detections
            ):
                await websocket.send_json({"error": "invalid detections payload"})
                continue
            if not isinstance(frame_number, int) or isinstance(frame_number, bool) or frame_number < 0:
                await websocket.send_json({"error": "frame must be a non-negative integer"})
                continue
            if not isinstance(jpeg_data, str):
                await websocket.send_json({"error": "message needs a base64 JPEG preview"})
                continue
            try:
                jpeg = base64.b64decode(jpeg_data, validate=True)
            except (binascii.Error, ValueError):
                await websocket.send_json({"error": "preview is not valid base64"})
                continue
            if not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 1_500_000:
                await websocket.send_json({"error": "preview must be a JPEG under 1.5 MB"})
                continue

            pose = message.get("pose")  # camera worker's auto-calibrated position (calib/auto_pose.py)
            if isinstance(pose, dict) and all(k in pose for k in ("R", "t", "pos", "yaw")):
                camera_config[camera_id].update({k: pose[k] for k in ("R", "t", "pos", "yaw")})
            async with state_lock:
                process_message(camera_id, message, jpeg)
            # server_t lets the worker map its clock onto ours (NTP-style, from the round trip)
            await websocket.send_json({"ok": True, "frame": frame_number, "server_t": time.time()})
    except WebSocketDisconnect:
        return
    finally:  # socket closed (or handler crashed): this camera is OFFLINE now
        async with state_lock:
            apply_transitions(registry.on_disconnect(camera_id, time.monotonic()))


@app.websocket("/tracks")
async def tracks(websocket: WebSocket) -> None:
    await websocket.accept()
    last_cameras = 0.0
    try:
        while True:
            async with state_lock:
                snapshot = {c: s for c, s in camera_state.items() if registry.active(c)}
                now = time.time()
                people = tracker.tracks(now)
                message = {"t": now, "tracks": people + map_tracks(snapshot)}
                if now - last_cameras >= 1.0:  # camera states every 1 s
                    message["cameras"] = cameras_status()
                    last_cameras = now
            await websocket.send_json(message)
            await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        return


@app.get("/camera/{camera_id}.mjpg")
async def camera_feed(camera_id: str) -> StreamingResponse:
    if camera_id not in camera_config:
        return JSONResponse({"error": f"Unknown camera {camera_id!r}"}, status_code=404)

    async def frames():
        last_frame = -1
        while True:
            async with state_lock:
                state = camera_state.get(camera_id)
            if state is not None and state["frame"] != last_frame:
                last_frame = state["frame"]
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(state["jpeg"])).encode("ascii")
                    + b"\r\n\r\n"
                    + state["jpeg"]
                    + b"\r\n"
                )
            await asyncio.sleep(0.05)

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    async with state_lock:
        camera_state.clear()
