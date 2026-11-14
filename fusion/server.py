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

    matched = match_floor_detections(observations, max_distance_m=MATCH_DISTANCE_METRES)
    tracks = []
    for track_id, detection in enumerate(matched, start=1):
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
    global camera_config, camera_state
    camera_config, _, _ = load_world_config()
    camera_state = {}


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
                }
                for camera_id in camera_config
            },
        }


@app.get("/api/world")
async def world() -> JSONResponse:
    _, walls, marker_data = load_world_config()
    markers = marker_data["markers"]
    xs = [float(marker["x"]) for marker in markers]
    ys = [float(marker["y"]) for marker in markers]
    padding = 0.5
    return JSONResponse(
        {
            "cameras": [
                {
                    "id": camera_id,
                    "pos": camera.get("pos", [0, 0, 0]),
                    "yaw": camera.get("yaw", 0),
                    "fov": camera.get("fov", 70),
                }
                for camera_id, camera in camera_config.items()
            ],
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

            async with state_lock:
                camera_state[camera_id] = {
                    "received_at": time.monotonic(),
                    "timestamp": float(message["t"]),
                    "frame": frame_number,
                    "detections": detections,
                    "jpeg": jpeg,
                }
            await websocket.send_json({"ok": True, "frame": frame_number})
    except WebSocketDisconnect:
        return


@app.websocket("/tracks")
async def tracks(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            async with state_lock:
                snapshot = dict(camera_state)
            await websocket.send_json({"t": time.time(), "tracks": map_tracks(snapshot)})
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
