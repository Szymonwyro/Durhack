"""Smoke test: run yolo11n on one frame from camera 0 (or the bundled ultralytics sample)."""
import time
from pathlib import Path

import cv2
import torch
from ultralytics import ASSETS, YOLO

ROOT = Path(__file__).resolve().parent.parent
dev = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"

cap = cv2.VideoCapture(0)
ok, frame = cap.read() if cap.isOpened() else (False, None)
cap.release()
src = "camera 0"
if not ok:
    frame = cv2.imread(str(ASSETS / "bus.jpg"))
    src = "ultralytics sample bus.jpg"
print("source:", src, frame.shape, "device:", dev)

model = YOLO(str(ROOT / "weights" / "yolo11n.pt"))
model(frame, device=dev, verbose=False)  # warmup
N = 20
t0 = time.time()
for _ in range(N):
    res = model(frame, device=dev, verbose=False)[0]
print(f"FPS ({N} runs): {N / (time.time() - t0):.1f}")
for b in res.boxes:
    print(f"  {res.names[int(b.cls)]:10s} conf={float(b.conf):.2f} xyxy={[round(v) for v in b.xyxy[0].tolist()]}")
