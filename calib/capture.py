"""Camera capture for Role 3 (and Role 2's workers): open by camera id from config/cameras.json,
timestamped frames, auto-reconnect, optional recording.

    from calib.capture import Camera, open_camera
    cam = open_camera("camA")            # source/width/height from config/cameras.json
    t, frame = cam.read()                # frame is None while reconnecting

CLI:
    python -m calib.capture camA                    # live preview (q to quit)
    python -m calib.capture camA --record 150       # record 150 s into recordings/
    python -m calib.capture --source 1 --snap       # one snapshot (identify which index is which)

OpenCV indices shift (e.g. when an iPhone attaches via Continuity Camera), so check the
resolution warning and snapshots, and keep config/cameras.json up to date on each machine.
"""
import argparse
import csv
import sys
import time
from pathlib import Path

import cv2

from calib.config_io import ROOT, get_camera, parse_source

RECORDINGS = ROOT / "recordings"


class Camera:
    def __init__(self, source, width=None, height=None, name="cam"):
        self.source, self.width, self.height, self.name = parse_source(source), width, height, name
        self.cap = None
        self._next_retry = 0.0
        self.open()

    def open(self):
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(self.source)
        if self.width and self.height:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        ok, frame = self.cap.read() if self.cap.isOpened() else (False, None)
        if not ok:
            print(f"[{self.name}] could not open source {self.source!r}; will retry", file=sys.stderr)
            return False
        h, w = frame.shape[:2]
        if self.width and (w, h) != (self.width, self.height):
            print(f"[{self.name}] WARNING: got {w}x{h}, config says {self.width}x{self.height}. "
                  f"Wrong camera index, or the camera doesn't support that size (K would be wrong).", file=sys.stderr)
        print(f"[{self.name}] opened {self.source!r} at {w}x{h}", file=sys.stderr)
        return True

    def read(self):
        """Return (unix_time, frame). frame is None if the camera dropped; reconnects every 2 s."""
        t = time.time()
        ok, frame = self.cap.read() if self.cap is not None else (False, None)
        if ok:
            return t, frame
        if t >= self._next_retry:
            self._next_retry = t + 2.0
            self.open()
        return t, None

    def release(self):
        if self.cap is not None:
            self.cap.release()


def open_camera(cam_id, source=None, width=None, height=None):
    """Open a camera by id from config/cameras.json; explicit args override the config."""
    cfg = get_camera(cam_id) or {}
    src = source if source is not None else cfg.get("source")
    if src is None:
        raise SystemExit(f"{cam_id}: no source in config/cameras.json; pass --source")
    return Camera(src, width or cfg.get("width"), height or cfg.get("height"), name=cam_id)


def main():
    p = argparse.ArgumentParser(description="Camera preview / record / snapshot")
    p.add_argument("cam_id", nargs="?", default="cam")
    p.add_argument("--source", help="OpenCV index or URL (overrides config)")
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--record", type=float, metavar="SECONDS", help="record a clip into recordings/")
    p.add_argument("--snap", action="store_true", help="save one snapshot into recordings/ and exit")
    a = p.parse_args()
    cam = open_camera(a.cam_id, a.source, a.width, a.height)
    RECORDINGS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")

    if a.snap:
        for _ in range(10):  # let auto-exposure settle
            t, frame = cam.read()
        if frame is None:
            sys.exit("no frame")
        out = RECORDINGS / f"{a.cam_id}_src{cam.source}_{stamp}.jpg"
        cv2.imwrite(str(out), frame)
        print(out)
        return

    writer = ts_file = None
    t_end = None
    if a.record:
        t, frame = cam.read()
        if frame is None:
            sys.exit("no frame to start recording")
        h, w = frame.shape[:2]
        base = RECORDINGS / f"{a.cam_id}_{stamp}"
        writer = cv2.VideoWriter(str(base) + ".mp4", cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
        ts_file = open(str(base) + "_timestamps.csv", "w", newline="")
        ts_csv = csv.writer(ts_file)
        ts_csv.writerow(["frame", "unix_t"])
        t_end = time.time() + a.record
        print(f"recording {a.record:.0f} s -> {base}.mp4 (+ _timestamps.csv)")

    n = 0
    try:
        while t_end is None or time.time() < t_end:
            t, frame = cam.read()
            if frame is None:
                time.sleep(0.05)
                continue
            if writer is not None:
                writer.write(frame)
                ts_csv.writerow([n, f"{t:.4f}"])
            n += 1
            view = frame.copy()
            label = f"{a.cam_id} src={cam.source} {frame.shape[1]}x{frame.shape[0]}"
            if t_end:
                label += f"  REC {t_end - time.time():.0f}s"
            cv2.putText(view, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            cv2.imshow("capture", view)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        if writer is not None:
            writer.release()
            ts_file.close()
            print(f"saved {n} frames")
        cam.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
