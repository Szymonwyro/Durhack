"""Show a live top-down floor map using one calibrated camera.

Run with: python -m tools.test_single_camera_map
The detected person's foot point or other object's box bottom is assumed to
touch the floor; this test does not estimate object height.
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from calib.config_io import parse_source
from perception.detect import FrameDetections, detect_video
from perception.floor_project import (
    CameraCalibration,
    MatchedFloorDetection,
    project_frame,
)
from tools.live_map import WALL_CONFIG, draw_map, map_bounds

ROOT = Path(__file__).resolve().parent.parent
CAMERA_CONFIG = ROOT / "config" / "cameras.json"
WINDOW_NAME = "Single-camera floor map (q to quit)"


class SingleCameraConfig(CameraCalibration):
    source: str | int


def load_camera(camera_id: str | None, source_override: str | None) -> SingleCameraConfig:
    if not CAMERA_CONFIG.is_file():
        raise FileNotFoundError(f"Camera config not found: {CAMERA_CONFIG}")

    data = json.loads(CAMERA_CONFIG.read_text(encoding="utf-8"))
    camera_list = data.get("cameras", [])
    if camera_id is None:
        calibrated = [
            camera for camera in camera_list
            if all(key in camera for key in ("source", "K", "dist", "R", "t"))
        ]
        if not calibrated:
            missing_by_camera = [
                f"{camera.get('id', '<unnamed>')}: "
                + ", ".join(
                    key for key in ("source", "K", "dist", "R", "t") if key not in camera
                )
                for camera in camera_list
            ]
            detail = "; ".join(missing_by_camera) or "no cameras configured"
            raise ValueError(
                f"No camera has complete calibration in {CAMERA_CONFIG} ({detail})"
            )
        if len(calibrated) > 1:
            raise ValueError(
                f"Expected exactly one camera with source, K, dist, R, and t in "
                f"{CAMERA_CONFIG}; found {len(calibrated)}. Pass --camera ID to select one."
            )
        selected = calibrated[0]
    else:
        selected = next((camera for camera in camera_list if camera.get("id") == camera_id), None)
        if selected is None:
            raise KeyError(f"Camera {camera_id!r} is not configured in {CAMERA_CONFIG}")

    missing = [key for key in ("K", "dist", "R", "t") if key not in selected]
    if "source" not in selected and source_override is None:
        missing.append("source (or pass --source)")
    if missing:
        raise ValueError(
            f"Camera {selected.get('id', camera_id)!r} is missing {', '.join(missing)}; "
            "intrinsics and extrinsics are both required for floor projection"
        )

    source = source_override if source_override is not None else selected["source"]
    return {
        "id": selected["id"],
        "source": parse_source(source),
        "K": selected["K"],
        "dist": selected["dist"],
        "R": selected["R"],
        "t": selected["t"],
    }


def load_walls() -> list[dict[str, object]]:
    if not WALL_CONFIG.is_file():
        return []
    data = json.loads(WALL_CONFIG.read_text(encoding="utf-8"))
    return data.get("walls", [])


CAMERA_WINDOW = "Camera view"


def floor_square_pixels(camera: SingleCameraConfig) -> np.ndarray | None:
    """Corner-marker square (config/markers.json) projected into the camera image, or None."""
    markers_path = ROOT / "config" / "markers.json"
    if not markers_path.is_file():
        return None
    markers = json.loads(markers_path.read_text(encoding="utf-8"))["markers"]
    centre = np.mean([[m["x"], m["y"]] for m in markers], axis=0)
    ordered = sorted(markers, key=lambda m: np.arctan2(m["y"] - centre[1], m["x"] - centre[0]))
    world = np.array([[m["x"], m["y"], 0.0] for m in ordered], np.float64)
    rvec, _ = cv2.Rodrigues(np.asarray(camera["R"], np.float64))
    pixels, _ = cv2.projectPoints(world, rvec, np.asarray(camera["t"], np.float64),
                                  np.asarray(camera["K"], np.float64), np.asarray(camera["dist"], np.float64))
    return pixels.reshape(-1, 2).astype(np.int32)


def draw_camera_view(image: np.ndarray, frame: FrameDetections, square: np.ndarray | None) -> np.ndarray:
    view = image.copy()
    if square is not None:
        cv2.polylines(view, [square], True, (0, 200, 255), 2)
    for det in frame["detections"]:
        x, y, w, h = (int(v) for v in det["bbox"])
        cv2.rectangle(view, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(view, f"{det['class_name']} {det['confidence']:.2f}", (x, max(20, y - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        if det.get("foot"):
            fx, fy = (int(v) for v in det["foot"])
            cv2.circle(view, (fx, fy), 7, (0, 0, 255), -1)
    return view


def as_map_detections(
    frame: FrameDetections,
    camera: SingleCameraConfig,
) -> list[MatchedFloorDetection]:
    projected = project_frame(frame, camera)
    return [
        {
            "class_id": detection["class_id"],
            "class_name": detection["class_name"],
            "position": detection["position"],
            "cameras": [camera["id"]],
            "confidence_by_camera": {camera["id"]: detection["confidence"]},
            "observations": [detection],
        }
        for detection in projected
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", help="Camera ID from config/cameras.json")
    parser.add_argument("--source", help="Override the configured video source or camera index")
    parser.add_argument(
        "--model",
        default=None,
        help="YOLO weights; defaults to weights/yolo11n.pt or yolo11n.pt",
    )
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum confidence, 0 to 1")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional YOLO class IDs, e.g. 0 for person")
    parser.add_argument("--device", help="Inference device, such as cpu, mps, or 0")
    parser.add_argument(
        "--bounds",
        type=float,
        nargs=4,
        metavar=("XMIN", "XMAX", "YMIN", "YMAX"),
        help="Map bounds in world metres; otherwise inferred from walls and camera position",
    )
    parser.add_argument("--show-camera", action="store_true",
                        help="Also show the camera view with boxes, foot points and the calibrated floor square")
    args = parser.parse_args()
    if not 0 <= args.confidence <= 1:
        parser.error("--confidence must be between 0 and 1")

    camera = load_camera(args.camera, args.source)
    walls = load_walls()
    bounds = map_bounds({camera["id"]: camera}, walls, args.bounds)
    model_path = args.model
    if model_path is None:
        local_weights = ROOT / "weights" / "yolo11n.pt"
        model_path = str(local_weights) if local_weights.is_file() else "yolo11n.pt"

    square = floor_square_pixels(camera) if args.show_camera else None

    def show_map(image: np.ndarray, frame: FrameDetections) -> bool:
        if args.show_camera:
            cv2.imshow(CAMERA_WINDOW, draw_camera_view(image, frame, square))
        detections = as_map_detections(frame, camera)
        canvas = draw_map(detections, walls, bounds, 1, [])
        cv2.putText(
            canvas,
            "Single-camera floor projection; object base assumed on floor",
            (75, 735),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.48,
            (70, 190, 255),
            1,
        )
        cv2.imshow(WINDOW_NAME, canvas)
        return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)

    try:
        for _ in detect_video(
            camera["source"],
            model_path=model_path,
            confidence=args.confidence,
            classes=args.classes,
            device=args.device,
            on_frame=show_map,
        ):
            pass
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
