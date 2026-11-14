"""Run YOLO on a video and return detected object positions in image pixels.

Positions use the image coordinate system (origin at the top-left). Person
detections also include a foot point, ready for floor projection.

Example:
    python perception/detect.py path\\to\\video.mp4
    python perception/detect.py 0 --classes 0
"""

import argparse
from collections.abc import Iterator, Sequence
import json
from pathlib import Path
import time
from typing import Callable, TypedDict

import cv2
import numpy as np


class Detection(TypedDict):
    class_id: int
    class_name: str
    confidence: float
    bbox: list[float]
    position: list[float]
    foot: list[float] | None


class FrameDetections(TypedDict):
    frame: int
    time_seconds: float
    timestamp: float
    width: int
    height: int
    detections: list[Detection]


def detect_video(
    video_source: str | int | Path,
    model_path: str | Path = "yolo11n.pt",
    confidence: float = 0.25,
    classes: Sequence[int] | None = None,
    device: str | None = None,
    on_frame: Callable[[np.ndarray, FrameDetections], bool] | None = None,
    width: int | None = None,
    height: int | None = None,
) -> Iterator[FrameDetections]:
    """Yield per-frame object detections; positions are pixels, not world coordinates.

    ``video_source`` may be a video path, stream URL, or camera index. If the
    model file is not present locally, Ultralytics may download it by name.
    ``on_frame``, when provided, receives each source frame and its detections;
    return False from it to stop after the current frame.
    """
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")

    source: str | int = str(video_source) if isinstance(video_source, Path) else video_source
    if width and height:  # request the calibrated size (cameras default to e.g. 640x480 on Windows)
        from calib.capture import open_capture

        capture = open_capture(source, width, height)
    else:
        capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        capture.release()
        raise OSError(f"Could not open video source: {video_source}")

    try:
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_index = 0

        while True:
            ok, frame = capture.read()
            if not ok:
                break

            result = model.predict(
                frame,
                conf=confidence,
                classes=list(classes) if classes is not None else None,
                device=device,
                verbose=False,
            )[0]
            frame_height, frame_width = frame.shape[:2]
            captured_at = time.time()
            time_seconds = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000
            if time_seconds <= 0 and fps > 0:
                time_seconds = frame_index / fps

            detections: list[Detection] = []
            for box in result.boxes:
                class_id = int(box.cls[0].item())
                class_name = result.names[class_id]
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                center_x = (x1 + x2) / 2
                center_y = (y1 + y2) / 2
                detections.append(
                    {
                        "class_id": class_id,
                        "class_name": class_name,
                        "confidence": float(box.conf[0].item()),
                        "bbox": [x1, y1, x2 - x1, y2 - y1],
                        "position": [center_x, center_y],
                        "foot": [center_x, y2] if class_name == "person" else None,
                    }
                )

            frame_result: FrameDetections = {
                "frame": frame_index,
                "time_seconds": time_seconds,
                "timestamp": captured_at,
                "width": frame_width,
                "height": frame_height,
                "detections": detections,
            }
            should_continue = on_frame(frame, frame_result) if on_frame is not None else True
            yield frame_result
            frame_index += 1
            if not should_continue:
                break
    finally:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="Video file, stream URL, or camera index (for example 0)")
    parser.add_argument("--model", default="yolo11n.pt", help="Ultralytics model weights")
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum confidence, 0 to 1")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional YOLO class IDs to detect")
    parser.add_argument("--device", help="Inference device, such as cpu, mps, or 0")
    args = parser.parse_args()

    source: str | int = int(args.video) if args.video.isdecimal() else args.video
    for frame_result in detect_video(
        source,
        model_path=args.model,
        confidence=args.confidence,
        classes=args.classes,
        device=args.device,
    ):
        print(json.dumps(frame_result))

if __name__ == "__main__":
    main()
