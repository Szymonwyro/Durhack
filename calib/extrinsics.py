"""Camera pose in the world frame from floor ArUco markers -> R, t (world -> camera) in config/cameras.json.

    python -m calib.extrinsics camA                 # live: averages corners over frames, S saves, Q quits
    python -m calib.extrinsics camB --image snap.jpg   # from one still (e.g. a snapshot copied off the UNO Q)

Needs K/dist for the camera first (calib/intrinsics.py) and marker positions in config/markers.json.
The camera must be FIXED (taped down) before running this. Moving it afterwards means re-running.
More markers spread across the view = better. One marker alone works but can be ambiguous/flip.
Check: the drawn world axes sit on marker ID 0 (x red, y green, z blue up), the reprojected
outlines (magenta) sit on the real markers, error < ~3 px, and the camera height looks right.
"""
import argparse
import math
import sys
import time

import cv2
import numpy as np

from calib.capture import open_camera
from calib.config_io import get_camera, load_markers, update_camera

ARUCO_DICTS = {"DICT_4X4_50": cv2.aruco.DICT_4X4_50}


def marker_world_corners(m, size):
    """Corners of a floor marker in world coords, in ArUco order (TL, TR, BR, BL of the printed marker).
    Marker frame: x = right on the print, y = towards the top of the print (away from the ID label), z = up."""
    h = size / 2
    yaw = math.radians(m.get("yaw_deg", 0.0))
    c, s = math.cos(yaw), math.sin(yaw)
    return np.array([[m["x"] + c * x - s * y, m["y"] + s * x + c * y, 0.0]
                     for x, y in [(-h, h), (h, h), (h, -h), (-h, -h)]], np.float64)


def make_detector(dict_name):
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    return cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(ARUCO_DICTS[dict_name]), params)


def detect(detector, frame):
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None:
        return {}
    return {int(i): c.reshape(4, 2) for i, c in zip(ids.ravel(), corners)}


def solve(seen, world, K, dist):
    """seen: {id: 4x2 image corners}; world: {id: 4x3}. Returns dict with R, t, errors... or None."""
    ids = [i for i in seen if i in world]
    if not ids:
        return None
    obj = np.concatenate([world[i] for i in ids])
    img = np.concatenate([seen[i] for i in ids]).astype(np.float64)
    n, rvecs, tvecs, errs = cv2.solvePnPGeneric(obj, img, K, dist, flags=cv2.SOLVEPNP_IPPE)
    if not n:
        return None
    rvec, tvec = rvecs[0], tvecs[0]
    errs = np.asarray(errs).ravel()
    ambiguous = n > 1 and errs[1] < 1.5 * errs[0] and len(ids) == 1
    rvec, tvec = cv2.solvePnPRefineLM(obj, img, K, dist, rvec, tvec)
    R, _ = cv2.Rodrigues(rvec)
    proj, _ = cv2.projectPoints(obj, rvec, tvec, K, dist)
    err = float(np.sqrt(np.mean(np.sum((proj.reshape(-1, 2) - img) ** 2, axis=1))))
    pos = (-R.T @ tvec).ravel()
    fwd = R.T @ np.array([0.0, 0.0, 1.0])  # camera optical axis in world
    return {"R": R, "t": tvec.ravel(), "rvec": rvec, "tvec": tvec, "err": err, "ids": ids, "pos": pos,
            "yaw": math.degrees(math.atan2(fwd[1], fwd[0])),
            "tilt": math.degrees(math.asin(max(-1.0, min(1.0, -fwd[2])))), "ambiguous": ambiguous}


def draw(view, sol, world, K, dist, seen):
    for i, c in seen.items():
        col = (0, 255, 0) if i in world else (0, 0, 255)
        cv2.polylines(view, [c.astype(np.int32)], True, col, 2)
        cv2.putText(view, str(i), tuple(c[0].astype(int)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, col, 2)
    if sol is None:
        return
    for i, wc in world.items():  # reprojected outlines of ALL known markers
        p, _ = cv2.projectPoints(wc, sol["rvec"], sol["tvec"], K, dist)
        cv2.polylines(view, [p.reshape(-1, 2).astype(np.int32)], True, (255, 0, 255), 1)
    axes = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]], np.float64)
    p, _ = cv2.projectPoints(axes, sol["rvec"], sol["tvec"], K, dist)
    p = p.reshape(-1, 2).astype(int)
    for k, col in zip(range(1, 4), [(0, 0, 255), (0, 255, 0), (255, 0, 0)]):
        cv2.line(view, tuple(p[0]), tuple(p[k]), col, 3)


def summary(sol):
    x, y, z = sol["pos"]
    s = (f"markers {sol['ids']}  reproj {sol['err']:.2f} px  camera at ({x:.2f}, {y:.2f}) m, "
         f"height {z:.2f} m, yaw {sol['yaw']:.0f}°, tilt down {sol['tilt']:.0f}°")
    warns = []
    if z <= 0:
        warns.append("camera BELOW the floor: check marker yaw/positions in markers.json")
    if sol["err"] > 3:
        warns.append("reprojection error high: check marker positions / K")
    if sol["ambiguous"]:
        warns.append("single marker, pose ambiguous: add more markers in view")
    return s, warns


def save(cam_id, sol):
    update_camera(cam_id, R=np.round(sol["R"], 6).tolist(), t=np.round(sol["t"], 4).tolist(),
                  pos=np.round(sol["pos"], 3).tolist(), yaw=round(sol["yaw"], 1),
                  extrinsics_rms_px=round(sol["err"], 2), extrinsics_markers=sol["ids"],
                  extrinsics_time=time.strftime("%Y-%m-%d %H:%M"))
    print(f"saved R/t for {cam_id} into config/cameras.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cam_id")
    p.add_argument("--source", help="OpenCV index or URL (overrides config)")
    p.add_argument("--image", help="solve from one image instead of live")
    p.add_argument("--frames", type=int, default=30, help="frames to average in live mode")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    cam_cfg = get_camera(a.cam_id)
    if not cam_cfg or "K" not in cam_cfg:
        sys.exit(f"{a.cam_id}: no K/dist in config/cameras.json; run calib.intrinsics first")
    K, dist = np.array(cam_cfg["K"], np.float64), np.array(cam_cfg["dist"], np.float64)
    mcfg = load_markers()
    world = {m["id"]: marker_world_corners(m, m.get("size_m", mcfg["size_m"])) for m in mcfg["markers"]}
    detector = make_detector(mcfg.get("dictionary", "DICT_4X4_50"))

    if a.image:
        frame = cv2.imread(a.image)
        if frame is None:
            sys.exit(f"can't read {a.image}")
        if (frame.shape[1], frame.shape[0]) != (cam_cfg["width"], cam_cfg["height"]):
            sys.exit(f"image is {frame.shape[1]}x{frame.shape[0]} but K is for {cam_cfg['width']}x{cam_cfg['height']}")
        seen = detect(detector, frame)
        print("detected ids:", sorted(seen), " known:", sorted(i for i in seen if i in world))
        sol = solve(seen, world, K, dist)
        if sol is None:
            sys.exit("no known markers visible")
        s, warns = summary(sol)
        print(s, *["WARNING: " + w for w in warns], sep="\n")
        if not a.dry_run:
            save(a.cam_id, sol)
        return

    cam = open_camera(a.cam_id, a.source)
    history = {}  # id -> list of 4x2 corners
    sol = None
    print("collecting... S = save, R = reset averaging, Q = quit")
    while True:
        t, frame = cam.read()
        if frame is None:
            time.sleep(0.05)
            continue
        seen = detect(detector, frame)
        for i, c in seen.items():
            history.setdefault(i, []).append(c)
            history[i] = history[i][-a.frames:]
        avg = {i: np.median(np.stack(cs), axis=0) for i, cs in history.items() if len(cs) >= min(5, a.frames)}
        sol = solve(avg, world, K, dist)
        view = frame.copy()
        draw(view, sol, world, K, dist, seen)
        lines = [f"{a.cam_id}: S save  R reset  Q quit"]
        if sol:
            s, warns = summary(sol)
            lines += [s[i:i + 95] for i in range(0, len(s), 95)] + ["! " + w for w in warns]
        else:
            lines.append("no known markers yet (green = known id, red = not in markers.json)")
        for k, line in enumerate(lines):
            cv2.putText(view, line, (10, 28 + 26 * k), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.imshow("extrinsics", view)
        key = cv2.waitKey(1) & 0xFF
        if key == ord("r"):
            history.clear()
        elif key == ord("s") and sol is not None:
            print(summary(sol)[0])
            if not a.dry_run:
                save(a.cam_id, sol)
            break
        elif key in (ord("q"), 27):
            print("quit without saving")
            break
    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
