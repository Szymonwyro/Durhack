"""Smoke test: open camera 0 and print detected DICT_4X4_50 marker IDs (verify printed markers).
Usage: test_aruco.py [camera_index] [seconds]   (Ctrl+C to stop early)"""
import sys
import time

import cv2

idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
secs = float(sys.argv[2]) if len(sys.argv) > 2 else 10
det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50))
cap = cv2.VideoCapture(idx)
if not cap.isOpened():
    sys.exit(f"camera {idx} not available")
print(f"watching camera {idx} for {secs:.0f}s ...")
last, t0 = None, time.time()
while time.time() - t0 < secs:
    ok, frame = cap.read()
    if not ok:
        continue
    _, ids, _ = det.detectMarkers(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    cur = sorted(ids.ravel().tolist()) if ids is not None else []
    if cur != last:
        print(f"{time.time() - t0:5.1f}s  ids: {cur}")
        last = cur
cap.release()
