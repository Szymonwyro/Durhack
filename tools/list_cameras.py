"""Smoke test: enumerate camera indices 0-5 and print resolution/FPS of each that opens.
On macOS the first run triggers a camera permission prompt for your terminal app.
An iPhone attached via Continuity Camera shows up as an extra camera and SHIFTS the indices,
so choose cameras by name/resolution in config/cameras.json, never by index alone."""
import time

import cv2

for i in range(6):
    cap = cv2.VideoCapture(i)
    if not cap.isOpened():
        print(f"cam {i}: not available")
        continue
    ok, frame = cap.read()
    if not ok:
        print(f"cam {i}: opened but no frame")
        cap.release()
        continue
    n, t0 = 0, time.time()
    while n < 30 and cap.read()[0]:
        n += 1
    fps = n / (time.time() - t0)
    print(f"cam {i}: {frame.shape[1]}x{frame.shape[0]}  reported {cap.get(cv2.CAP_PROP_FPS):.0f} fps, measured {fps:.1f} fps")
    cap.release()
