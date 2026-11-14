"""Calibrate WHERE a camera is (its position on the floor map), from the corner markers. Anyone can run it
on the laptop the camera is plugged into (Mac or Windows):

    python -m calib.position camB                  # pick the camera from a preview grid, then calibrate
    python -m calib.position camB --source 1       # skip the picker (OpenCV index or stream URL)
    .venv\\Scripts\\python -m calib.position camB   # same on Windows (PowerShell)

Before: the camera is mounted and taped down, the lens calibration (K) for it exists in config/cameras.json
(chessboard: python -m calib.intrinsics <cam> --source <N>), and as many of the corner markers 1, 3, 5, 7 as
possible are in view (all 4 is best; 2-3 is fine; 1 can flip).
Window: green = known marker, magenta = where the solved pose says the markers are (should sit on them),
red/green/blue axes on marker ID 1. S = save, R = restart averaging, Q = quit.
After saving it offers to commit + push config/cameras.json (only this camera's entry changes).

It does NOT change the camera's `source` (indices are per laptop) or the marker rotations in markers.json.
"""
import argparse
import socket
import subprocess
import sys
import time

import cv2
import numpy as np

from calib.capture import Camera
from calib.config_io import CAMERAS, ROOT, get_camera, load_markers, parse_source, update_camera
from calib.extrinsics import detect, draw, make_detector, marker_world_corners, solve, summary

GOOD_PX, OK_PX = 3.0, 6.0


def pick_source(max_index=5):
    """Show every local camera side by side; the user presses its number."""
    caps = {}
    for i in range(max_index + 1):
        cap = cv2.VideoCapture(i)
        if cap.isOpened() and cap.read()[0]:
            caps[i] = cap
        else:
            cap.release()
    if not caps:
        sys.exit("no cameras found. Close apps using the camera (Teams/Zoom/Camera); on Windows allow desktop apps in "
                 "Settings > Privacy & security > Camera; on macOS allow the terminal in Privacy > Camera.")
    if len(caps) == 1:
        (i, cap), = caps.items()
        cap.release()
        print(f"only one camera found: index {i}")
        return i
    print("press the number of the camera to calibrate (Q to quit)")
    try:
        while True:
            tiles = []
            for i, cap in caps.items():
                ok, f = cap.read()
                f = cv2.resize(f, (480, 270)) if ok else np.zeros((270, 480, 3), np.uint8)
                h, w = (cap.get(cv2.CAP_PROP_FRAME_HEIGHT), cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                cv2.putText(f, f"{i}  ({w:.0f}x{h:.0f})", (10, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 255, 255), 3)
                tiles.append(f)
            cv2.imshow("pick camera: press its number", np.hstack(tiles) if len(tiles) <= 3 else np.vstack(
                [np.hstack(tiles[k:k + 3] + [np.zeros_like(tiles[0])] * (3 - len(tiles[k:k + 3])))
                 for k in range(0, len(tiles), 3)]))
            key = cv2.waitKey(30) & 0xFF
            if key in (ord("q"), 27):
                sys.exit("quit")
            if ord("0") <= key <= ord("9") and key - ord("0") in caps:
                return key - ord("0")
    finally:
        for cap in caps.values():
            cap.release()
        cv2.destroyAllWindows()


def fit_intrinsics(cfg, w, h):
    """K/dist for the actual frame size. Same aspect ratio -> scale K; different -> can't (needs chessboard)."""
    K, dist = np.array(cfg["K"], np.float64), np.array(cfg["dist"], np.float64)
    W, H = cfg["width"], cfg["height"]
    if (w, h) == (W, H):
        return K, dist, None
    if abs(w / h - W / H) > 0.01:
        return None, None, (f"camera gives {w}x{h} but K is for {W}x{H} (different shape, can't scale). Run the "
                            f"chessboard calibration for this camera first: python -m calib.intrinsics {cfg['id']} "
                            f"--source <N>")
    s = w / W
    K[:2] *= s
    return K, dist, f"K scaled from {W}x{H} to {w}x{h}"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def commit_and_push(cam_id, msg):
    if input("commit + push config/cameras.json now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
        print(f"not pushed. Later: git add config/cameras.json && git commit -m \"{msg}\" && git pull --rebase && git push")
        return
    steps = [("add", "config/cameras.json"), ("commit", "-m", msg, "--", "config/cameras.json"),
             ("pull", "--rebase", "--autostash"), ("push",)]
    for step in steps:
        r = git(*step)
        if r.returncode != 0:
            print(f"git {step[0]} failed:\n{r.stdout}{r.stderr}\nFix it (or ask Role 3), then: git pull --rebase && git push")
            return
    print("pushed. Everyone else: git pull")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cam_id", help="camera name in config/cameras.json, e.g. camB")
    p.add_argument("--source", type=parse_source, help="OpenCV index or stream URL (default: pick from a preview)")
    p.add_argument("--frames", type=int, default=30, help="frames to average")
    p.add_argument("--no-push", action="store_true", help="save locally only, don't offer to commit/push")
    a = p.parse_args()

    git("pull", "--rebase", "--autostash")  # best effort: start from the latest config
    cfg = get_camera(a.cam_id)
    if not cfg or "K" not in cfg:  # new camera: still show the picker so the user learns its number
        source = a.source if a.source is not None else pick_source()
        sys.exit(f"{a.cam_id} is camera {source!r}, but it has no lens calibration (K) yet. Run the chessboard first:\n"
                 f"  python -m calib.intrinsics {a.cam_id} --source {source} --width 1280 --height 720")
    source = a.source if a.source is not None else pick_source()

    cam = Camera(source, cfg["width"], cfg["height"], name=a.cam_id)
    frame, deadline = None, time.time() + 8
    while frame is None and time.time() < deadline:
        _, frame = cam.read()
    if frame is None:
        sys.exit(f"no frames from {source!r}: wrong index, or another app is using the camera")
    h, w = frame.shape[:2]
    K, dist, note = fit_intrinsics(cfg, w, h)
    if K is None:
        sys.exit(note)
    if note:
        print(note)

    mcfg = load_markers()
    world = {m["id"]: marker_world_corners(m, m.get("size_m", mcfg["size_m"])) for m in mcfg["markers"]}
    detector = make_detector(mcfg.get("dictionary", "DICT_4X4_50"))
    history, sol = {}, None
    print(f"{a.cam_id} at {w}x{h}: S save, R restart averaging, Q quit")
    while True:
        _, frame = cam.read()
        if frame is None:
            continue
        seen = detect(detector, frame)
        for i, c in seen.items():
            history[i] = (history.get(i, []) + [c])[-a.frames:]
        avg = {i: np.median(np.stack(cs), axis=0) for i, cs in history.items() if len(cs) >= min(5, a.frames)}
        sol = solve(avg, world, K, dist)
        view = frame.copy()
        draw(view, sol, world, K, dist, seen)
        if sol is None:
            verdict, col = "NO CORNER MARKERS IN VIEW (need IDs 1/3/5/7)", (0, 0, 255)
            lines = []
        else:
            s, warns = summary(sol)
            bad = sol["pos"][2] <= 0 or sol["ambiguous"]
            verdict, col = (("BAD - don't save", (0, 0, 255)) if bad or sol["err"] > OK_PX else
                            ("GOOD - press S", (0, 200, 0)) if sol["err"] <= GOOD_PX and len(sol["ids"]) >= 2 else
                            ("OK - press S, or get more markers in view", (0, 200, 255)))
            lines = [s[k:k + 95] for k in range(0, len(s), 95)] + ["! " + x for x in warns]
        cv2.putText(view, verdict, (10, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 3)
        for k, line in enumerate(lines + ["S save   R restart   Q quit"]):
            cv2.putText(view, line, (10, 68 + 24 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        cv2.imshow(f"{a.cam_id} position", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("r"):
            history.clear()
        elif key in (ord("q"), 27):
            cam.release()
            cv2.destroyAllWindows()
            sys.exit("quit without saving")
        elif key == ord("s") and sol is not None:
            break
    snap = ROOT / "recordings" / "position" / f"{a.cam_id}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    snap.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(snap), frame)
    cam.release()
    cv2.destroyAllWindows()

    s, warns = summary(sol)
    print(s, *["WARNING: " + x for x in warns], sep="\n")
    fields = dict(R=np.round(sol["R"], 6).tolist(), t=np.round(sol["t"], 4).tolist(),
                  pos=np.round(sol["pos"], 3).tolist(), yaw=round(sol["yaw"], 1),
                  extrinsics_rms_px=round(sol["err"], 2), extrinsics_markers=sorted(sol["ids"]),
                  extrinsics_time=time.strftime("%Y-%m-%d %H:%M"), extrinsics_host=socket.gethostname())
    if note:  # frame size differs from K's: store the scaled K so the map uses the right one
        fields.update(width=w, height=h, K=np.round(K, 3).tolist())
    update_camera(a.cam_id, **fields)
    print(f"saved {a.cam_id} position into {CAMERAS.relative_to(ROOT)}  (snapshot: {snap.relative_to(ROOT)})")
    print("Don't move the camera now. If it moves, run this again.")
    if not a.no_push:
        x, y, z = sol["pos"]
        commit_and_push(a.cam_id, f"[calib] {a.cam_id} position ({x:.2f}, {y:.2f}, h {z:.2f} m), "
                                  f"{sol['err']:.1f} px, markers {sorted(sol['ids'])}")


if __name__ == "__main__":
    main()
