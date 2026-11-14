"""Display live webcam detections produced by perception.detect."""

import argparse
from pathlib import Path
import time

import cv2
import numpy as np

from perception.detect import FrameDetections, detect_video

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHTS = ROOT / "weights" / "yolo11n.pt"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    parser.add_argument(
        "--model",
        default=str(DEFAULT_WEIGHTS) if DEFAULT_WEIGHTS.is_file() else "yolo11n.pt",
        help="Model weights (downloads yolo11n.pt if not found locally)",
    )
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum confidence, 0 to 1")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional class IDs, e.g. 0 for person")
    parser.add_argument("--device", help="Inference device, such as cpu, mps, or 0")
    args = parser.parse_args()

    if not 0 <= args.confidence <= 1:
        parser.error("--confidence must be between 0 and 1")

    window_name = "Live object detection (press q to quit)"
    previous_frame_time = time.perf_counter()

    def show_detections(frame: np.ndarray, result: FrameDetections) -> bool:
        nonlocal previous_frame_time
        now = time.perf_counter()
        fps = 1 / max(now - previous_frame_time, 1e-9)
        previous_frame_time = now

        for detection in result["detections"]:
            x, y, width, height = detection["bbox"]
            top_left = (round(x), round(y))
            bottom_right = (round(x + width), round(y + height))
            cv2.rectangle(frame, top_left, bottom_right, (0, 255, 0), 2)

            label = f"{detection['class_name']} {detection['confidence']:.2f}"
            label_y = max(20, top_left[1] - 8)
            cv2.putText(
                frame,
                label,
                (top_left[0], label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )

        cv2.putText(
            frame,
            f"{fps:.1f} FPS | q to quit",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )
        cv2.imshow(window_name, frame)
        return cv2.waitKey(1) & 0xFF != ord("q")

    try:
        for _ in detect_video(
            args.camera,
            model_path=args.model,
            confidence=args.confidence,
            classes=args.classes,
            device=args.device,
            on_frame=show_detections,
        ):
            pass
    finally:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
