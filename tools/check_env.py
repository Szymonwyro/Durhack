"""Smoke test: import every dependency, print versions and the torch device."""
import importlib
import sys

MODS = ["cv2", "numpy", "scipy", "filterpy", "ultralytics", "mediapipe", "fastapi", "uvicorn",
        "websockets", "multipart", "PIL", "reportlab", "elevenlabs", "google.genai", "torch"]

print("python", sys.version.split()[0])
bad = []
for name in MODS:
    try:
        m = importlib.import_module(name)
        print(f"  OK   {name:12s} {getattr(m, '__version__', '?')}")
    except Exception as e:  # noqa: BLE001
        bad.append(name)
        print(f"  FAIL {name:12s} {e}")

import cv2  # noqa: E402
import torch  # noqa: E402

print("cv2.aruco.ArucoDetector:", hasattr(cv2, "aruco") and hasattr(cv2.aruco, "ArucoDetector"))
dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
print("torch device:", dev)
sys.exit(1 if bad else 0)
