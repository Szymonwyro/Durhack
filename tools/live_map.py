"""Run configured cameras and display matched detections on a live top-down map.

Requires at least two calibrated camera entries in config/cameras.json.
Run with: python -m tools.live_map
"""

import argparse
import json
import threading
import time
from pathlib import Path
from typing import TypedDict

import cv2
import numpy as np

from calib.config_io import parse_source
from perception.detect import FrameDetections, detect_video
from perception.floor_project import (
    CameraCalibration,
    MatchedFloorDetection,
    match_floor_detections,
    project_frame,
)

ROOT = Path(__file__).resolve().parent.parent
CAMERA_CONFIG = ROOT / "config" / "cameras.json"
WALL_CONFIG = ROOT / "config" / "walls.json"
WINDOW_NAME = "Live top-down object map (q to quit)"


class CameraConfig(CameraCalibration):
    source: str | int


class LatestFrame(TypedDict):
    received_at: float
    result: FrameDetections


def load_config() -> tuple[dict[str, CameraConfig], list[dict[str, object]]]:
    if not CAMERA_CONFIG.is_file():
        raise FileNotFoundError(f"Camera config not found: {CAMERA_CONFIG}")

    data = json.loads(CAMERA_CONFIG.read_text(encoding="utf-8"))
    camera_list = data.get("cameras", [])
    cameras: dict[str, CameraConfig] = {}
    for camera in camera_list:
        camera_id = camera.get("id")
        if not camera_id:
            raise ValueError("Every camera entry needs an id")
        missing = [key for key in ("source", "K", "dist", "R", "t") if key not in camera]
        if missing:
            raise ValueError(
                f"Camera {camera_id!r} is missing {', '.join(missing)}; "
                "run camera calibration first"
            )
        cameras[camera_id] = {
            "id": camera_id,
            "source": parse_source(camera["source"]),
            "K": camera["K"],
            "dist": camera["dist"],
            "R": camera["R"],
            "t": camera["t"],
        }
    if len(cameras) < 2:
        raise ValueError(
            f"At least two calibrated cameras are required in {CAMERA_CONFIG}; "
            f"found {len(cameras)}"
        )

    walls: list[dict[str, object]] = []
    if WALL_CONFIG.is_file():
        wall_data = json.loads(WALL_CONFIG.read_text(encoding="utf-8"))
        walls = wall_data.get("walls", [])
    return cameras, walls


def map_bounds(
    cameras: dict[str, CameraConfig],
    walls: list[dict[str, object]],
    requested: list[float] | None,
) -> tuple[float, float, float, float]:
    if requested is not None:
        x_min, x_max, y_min, y_max = requested
        if x_min >= x_max or y_min >= y_max:
            raise ValueError("--bounds must be xmin xmax ymin ymax with min < max")
        return x_min, x_max, y_min, y_max

    points: list[list[float]] = []
    for wall in walls:
        points.extend((wall["a"], wall["b"]))
    for camera in cameras.values():
        rotation = np.asarray(camera["R"], dtype=np.float64)
        translation = np.asarray(camera["t"], dtype=np.float64).reshape(3)
        position = -rotation.T @ translation
        points.append([float(position[0]), float(position[1])])

    coordinates = np.asarray(points, dtype=np.float64)
    if not np.isfinite(coordinates).all():
        raise ValueError("Camera and wall positions must be finite")
    minimum = coordinates.min(axis=0) - 1.0
    maximum = coordinates.max(axis=0) + 1.0
    for axis in range(2):
        if maximum[axis] - minimum[axis] < 4:
            centre = (maximum[axis] + minimum[axis]) / 2
            minimum[axis], maximum[axis] = centre - 2, centre + 2
    return float(minimum[0]), float(maximum[0]), float(minimum[1]), float(maximum[1])


def draw_map(
    detections: list[MatchedFloorDetection],
    walls: list[dict[str, object]],
    bounds: tuple[float, float, float, float],
    camera_count: int,
    stale_cameras: list[str],
) -> np.ndarray:
    canvas_width, canvas_height = 1100, 780
    left, top, right, bottom = 75, 65, 1030, 710
    x_min, x_max, y_min, y_max = bounds
    scale = min((right - left) / (x_max - x_min), (bottom - top) / (y_max - y_min))
    map_width, map_height = (x_max - x_min) * scale, (y_max - y_min) * scale
    map_left = left + ((right - left) - map_width) / 2
    map_bottom = top + ((bottom - top) - map_height) / 2 + map_height
    canvas = np.full((canvas_height, canvas_width, 3), (25, 30, 34), dtype=np.uint8)

    def to_pixel(point: list[float] | tuple[float, float]) -> tuple[int, int]:
        px = map_left + (point[0] - x_min) * scale
        py = map_bottom - (point[1] - y_min) * scale
        return round(px), round(py)

    cv2.rectangle(
        canvas,
        (round(map_left), round(map_bottom - map_height)),
        (round(map_left + map_width), round(map_bottom)),
        (75, 82, 87),
        1,
    )
    grid_step = 1.0
    for x_value in np.arange(np.ceil(x_min / grid_step) * grid_step, x_max, grid_step):
        px, _ = to_pixel((x_value, y_min))
        cv2.line(canvas, (px, round(map_bottom - map_height)), (px, round(map_bottom)), (42, 48, 52), 1)
        cv2.putText(canvas, f"{x_value:g}", (px + 3, round(map_bottom + 17)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (145, 150, 154), 1)
    for y_value in np.arange(np.ceil(y_min / grid_step) * grid_step, y_max, grid_step):
        _, py = to_pixel((x_min, y_value))
        cv2.line(canvas, (round(map_left), py), (round(map_left + map_width), py), (42, 48, 52), 1)
        cv2.putText(canvas, f"{y_value:g}", (round(map_left - 30), py + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (145, 150, 154), 1)

    for wall in walls:
        a = wall["a"]
        b = wall["b"]
        cv2.line(canvas, to_pixel(a), to_pixel(b), (120, 120, 120), 6, cv2.LINE_AA)

    palette = [(0, 220, 255), (40, 170, 255), (80, 230, 100), (255, 170, 60), (230, 100, 220)]
    for detection in detections:
        point = to_pixel(detection["position"])
        color = palette[detection["class_id"] % len(palette)]
        radius = 9 if len(detection["cameras"]) > 1 else 6
        cv2.circle(canvas, point, radius, color, -1, cv2.LINE_AA)
        cv2.circle(canvas, point, radius + 2, (245, 245, 245), 1, cv2.LINE_AA)
        x, y = detection["position"]
        cameras_text = ",".join(detection["cameras"])
        label = f"{detection['class_name']} {x:.2f},{y:.2f}m [{cameras_text}]"
        cv2.putText(canvas, label, (point[0] + 10, point[1] - 8),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (240, 240, 240), 1, cv2.LINE_AA)

    cv2.putText(canvas, "TOP-DOWN MAP | positions in metres (X right, Y up)",
                (left, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2)
    status = f"{len(detections)} objects | {camera_count} cameras"
    if stale_cameras:
        status += f" | waiting for: {', '.join(stale_cameras)}"
    cv2.putText(canvas, status, (left, 750), cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (70, 190, 255) if stale_cameras else (160, 220, 160), 1)
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=None, help="YOLO weights; defaults to weights/yolo11n.pt or yolo11n.pt")
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum detection confidence, 0 to 1")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional YOLO class IDs, e.g. 0 for person")
    parser.add_argument("--device", help="Inference device, such as cpu, mps, or 0")
    parser.add_argument("--match-distance", type=float, default=0.75,
                        help="Maximum cross-camera floor distance for a match, in metres")
    parser.add_argument("--max-frame-age", type=float, default=0.75,
                        help="Ignore camera frames older than this many seconds")
    parser.add_argument("--bounds", type=float, nargs=4, metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
                        help="Map bounds in world metres; otherwise inferred from walls/camera positions")
    args = parser.parse_args()
    if not 0 <= args.confidence <= 1:
        parser.error("--confidence must be between 0 and 1")
    if args.match_distance <= 0 or args.max_frame_age <= 0:
        parser.error("--match-distance and --max-frame-age must be positive")

    cameras, walls = load_config()
    bounds = map_bounds(cameras, walls, args.bounds)
    model_path = args.model
    if model_path is None:
        local_weights = ROOT / "weights" / "yolo11n.pt"
        model_path = str(local_weights) if local_weights.is_file() else "yolo11n.pt"

    latest: dict[str, LatestFrame] = {}
    errors: dict[str, BaseException] = {}
    lock = threading.Lock()
    stop = threading.Event()

    def run_camera(camera_id: str, camera: CameraConfig) -> None:
        def receive_frame(image: np.ndarray, result: FrameDetections) -> bool:
            del image
            with lock:
                latest[camera_id] = {"received_at": time.monotonic(), "result": result}
            return not stop.is_set()

        try:
            for _ in detect_video(
                camera["source"],
                model_path=model_path,
                confidence=args.confidence,
                classes=args.classes,
                device=args.device,
                on_frame=receive_frame,
            ):
                if stop.is_set():
                    break
        except Exception as error:
            with lock:
                errors[camera_id] = error

    workers = [
        threading.Thread(
            target=run_camera,
            args=(camera_id, camera),
            name=f"detection-{camera_id}",
            daemon=True,
        )
        for camera_id, camera in cameras.items()
    ]
    for worker in workers:
        worker.start()

    previous_signature: tuple[tuple[str, int], ...] | None = None
    matched: list[MatchedFloorDetection] = []
    try:
        while True:
            now = time.monotonic()
            with lock:
                if errors:
                    camera_id, error = next(iter(errors.items()))
                    raise RuntimeError(f"Camera {camera_id!r} detection failed: {error}") from error
                snapshot = dict(latest)

            active = {
                camera_id: frame
                for camera_id, frame in snapshot.items()
                if now - frame["received_at"] <= args.max_frame_age
            }
            signature = tuple(
                sorted((camera_id, frame["result"]["frame"]) for camera_id, frame in active.items())
            )
            if signature != previous_signature:
                floor_detections = {
                    camera_id: project_frame(frame["result"], cameras[camera_id])
                    for camera_id, frame in active.items()
                }
                matched = match_floor_detections(
                    floor_detections,
                    max_distance_m=args.match_distance,
                )
                previous_signature = signature

            stale = sorted(set(cameras) - set(active))
            cv2.imshow(WINDOW_NAME, draw_map(matched, walls, bounds, len(cameras), stale))
            key = cv2.waitKey(20) & 0xFF
            if key in (ord("q"), 27):
                break
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=2)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
