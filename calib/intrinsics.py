"""Lens calibration (K, dist) from a chessboard -> config/cameras.json.

    python -m calib.intrinsics camB --source 1 --width 640 --height 480
    python -m calib.intrinsics camB --square-mm 31.4      # optional: square size doesn't change K/dist,
                                                         # so a chessboard shown full-screen on a laptop/iPad works
    python -m calib.intrinsics camB --images "recordings/calib/camB/*.jpg"   # redo offline from saved views

Live mode auto-captures a view when the board is found, still, and in a new position (SPACE forces one,
C calibrates early, Q/ESC quits). Move the board around the WHOLE image, including the corners, and tilt it
up to ~45°. Aim for RMS < 0.5 px (< 1.0 px is usable).
Intrinsics belong to the camera + resolution, not the host: the UNO Q's USB camera can be calibrated on a Mac.
Views are saved to recordings/calib/<cam>/ so you can re-run offline.
"""
import argparse
import glob
import sys
import time

import cv2
import numpy as np

from calib.capture import open_camera
from calib.config_io import ROOT, get_camera, update_camera


def board_points(cols, rows, square_m):
    obj = np.zeros((rows * cols, 3), np.float32)
    obj[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2) * square_m
    return obj


def find_board(gray, cols, rows):
    ok, corners = cv2.findChessboardCornersSB(gray, (cols, rows), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    return corners.reshape(-1, 1, 2).astype(np.float32) if ok else None  # OpenCV 5 may return (N, 2)


def calibrate(views, size, cols, rows, square_m):
    obj = board_points(cols, rows, square_m)
    objs = [obj] * len(views)
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(objs, views, size, None, None)
    per_view = []
    for o, img, r, t in zip(objs, views, rvecs, tvecs):
        proj, _ = cv2.projectPoints(o, r, t, K, dist)
        per_view.append(float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img.reshape(-1, 2)) ** 2, axis=1)))))
    return rms, K, dist, per_view


def calibrate_robust(views, size, cols, rows, square_m):
    """Calibrate, drop views with > 2x the median error once, and recalibrate."""
    rms, K, dist, per_view = calibrate(views, size, cols, rows, square_m)
    med = float(np.median(per_view))
    keep = [v for v, e in zip(views, per_view) if e <= max(2 * med, 0.3)]
    if len(keep) < len(views) and len(keep) >= 8:
        print(f"dropping {len(views) - len(keep)} noisy view(s) (> 2x median error {med:.2f} px)")
        rms, K, dist, per_view = calibrate(keep, size, cols, rows, square_m)
    return rms, K, dist, per_view, len(keep)


def save(cam_id, K, dist, size, rms, source=None):
    fields = {"width": size[0], "height": size[1], "K": np.round(K, 3).tolist(),
              "dist": np.round(dist.ravel(), 6).tolist(), "intrinsics_rms_px": round(float(rms), 3),
              "fov": round(float(np.degrees(2 * np.arctan(size[0] / (2 * K[0, 0])))), 1)}
    if source is not None and "source" not in (get_camera(cam_id) or {}):
        fields["source"] = source  # only for a new camera: indices are per laptop, don't overwrite e.g. a relay URL
    update_camera(cam_id, **fields)
    print(f"saved K/dist for {cam_id} into config/cameras.json  (horizontal FOV {fields['fov']}°)")


def report(rms, K, per_view, n):
    print(f"\nviews used: {n}   RMS reprojection error: {rms:.3f} px   "
          f"({'GOOD' if rms < 0.5 else 'OK' if rms < 1.0 else 'BAD - recapture'})")
    print(f"fx={K[0, 0]:.1f} fy={K[1, 1]:.1f} cx={K[0, 2]:.1f} cy={K[1, 2]:.1f}   worst view {max(per_view):.2f} px")


def run_offline(a, square_m):
    files = sorted(glob.glob(a.images))
    views, size = [], None
    for f in files:
        gray = cv2.imread(f, cv2.IMREAD_GRAYSCALE)
        if gray is None:
            continue
        size = (gray.shape[1], gray.shape[0])
        c = find_board(gray, a.cols, a.rows)
        print(f"{f}: {'board' if c is not None else 'no board'}")
        if c is not None:
            views.append(c)
    if len(views) < 8:
        sys.exit(f"only {len(views)} usable views; need >= 8")
    rms, K, dist, per_view, n = calibrate_robust(views, size, a.cols, a.rows, square_m)
    report(rms, K, per_view, n)
    if not a.dry_run:
        save(a.cam_id, K, dist, size, rms)


def run_live(a, square_m):
    cam = open_camera(a.cam_id, a.source, a.width, a.height)
    out_dir = ROOT / "recordings" / "calib" / a.cam_id
    out_dir.mkdir(parents=True, exist_ok=True)
    views, last_corners, last_t, prev = [], [], 0.0, None
    size = None
    while True:
        t, frame = cam.read()
        if frame is None:
            time.sleep(0.05)
            continue
        size = (frame.shape[1], frame.shape[0])
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners = find_board(gray, a.cols, a.rows)
        view = frame.copy()
        for c in last_corners:  # coverage so far
            for p in c.reshape(-1, 2)[:: max(1, len(c) // 12)]:
                cv2.circle(view, (int(p[0]), int(p[1])), 3, (255, 128, 0), -1)
        key = cv2.waitKey(1) & 0xFF
        grab = key == ord(" ")
        if corners is not None:
            cv2.drawChessboardCorners(view, (a.cols, a.rows), corners, True)
            still = prev is not None and np.abs(corners - prev).max() < 2.0
            centre = corners.reshape(-1, 2).mean(0)
            spread = np.ptp(corners.reshape(-1, 2), axis=0).max()
            new_pose = all(np.linalg.norm(centre - c.reshape(-1, 2).mean(0)) > 0.08 * size[0]
                           or abs(spread - np.ptp(c.reshape(-1, 2), axis=0).max()) > 0.15 * spread
                           for c in last_corners)
            if still and new_pose and t - last_t > 0.7:
                grab = True
            prev = corners
        else:
            prev = None
        if grab and corners is not None:
            views.append(corners)
            last_corners.append(corners)
            last_t = t
            cv2.imwrite(str(out_dir / f"view_{len(views):02d}.jpg"), frame)
            view[:] = 255 - view  # flash
        msg = f"{a.cam_id}: {len(views)}/{a.views} views  (SPACE grab, C calibrate, Q quit)"
        cv2.putText(view, msg, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(view, "move board everywhere incl. corners; tilt it", (10, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow("intrinsics", view)
        if key in (ord("q"), 27):
            cam.release()
            cv2.destroyAllWindows()
            sys.exit("aborted, nothing saved")
        if len(views) >= a.views or (key == ord("c") and len(views) >= 8):
            break
    cam.release()
    cv2.destroyAllWindows()
    rms, K, dist, per_view, n = calibrate_robust(views, size, a.cols, a.rows, square_m)
    report(rms, K, per_view, n)
    print(f"views saved in {out_dir}")
    if not a.dry_run:
        save(a.cam_id, K, dist, size, rms, source=cam.source)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cam_id", nargs="?", default="camB", help="camera ID to update (default: camB)")
    p.add_argument("--source", help="OpenCV index or URL (overrides config)")
    p.add_argument("--width", type=int, default=None, help="capture width (default: config, else camera default)")
    p.add_argument("--height", type=int, default=None)
    p.add_argument("--cols", type=int, default=9, help="inner corners across (default 9)")
    p.add_argument("--rows", type=int, default=6, help="inner corners down (default 6)")
    p.add_argument("--square-mm", type=float, default=25.0, help="MEASURED square size (default 25)")
    p.add_argument("--views", type=int, default=20)
    p.add_argument("--images", help="glob of saved images: calibrate offline")
    p.add_argument("--dry-run", action="store_true", help="don't write config/cameras.json")
    a = p.parse_args()
    if a.source is None and get_camera(a.cam_id) is None and not a.images:
        sys.exit(f"{a.cam_id} isn't in config/cameras.json yet; pass --source <index>")
    square_m = a.square_mm / 1000.0
    run_offline(a, square_m) if a.images else run_live(a, square_m)


if __name__ == "__main__":
    main()
