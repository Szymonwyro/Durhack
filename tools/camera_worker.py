"""Detect and publish one local camera to the shared LAN fusion server.

Example: python -m tools.camera_worker camB --source 1 --server ws://192.168.1.20:8000/ingest/camB
"""

import argparse
import asyncio
import base64
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any

import cv2
import numpy as np

from calib.auto_pose import AutoPose
from calib.refresh import DEFAULTS, load_thresholds
from calib.config_io import get_camera, parse_source
from perception.detect import FrameDetections, detect_video
from perception.skeleton import BONES, JOINT_MIN_CONF, lift_keypoints
from perception.floor_project import CameraCalibration, project_detection

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WEIGHTS = ROOT / "weights" / "yolo11n.pt"
POSE_WEIGHTS = ROOT / "weights" / "yolo11n-pose.pt"
MAX_PREVIEW_WIDTH = 640


def load_camera(camera_id: str, source_override: str | None) -> tuple[CameraCalibration, str | int]:
    config = get_camera(camera_id)
    if config is None:
        raise KeyError(f"Camera {camera_id!r} is not in config/cameras.json")
    missing = [key for key in ("K", "dist") if key not in config]  # R/t may come from auto-pose
    if missing:
        raise ValueError(f"Camera {camera_id!r} needs calibration fields: {', '.join(missing)}")
    source = source_override if source_override is not None else config.get("source")
    if source is None:
        raise ValueError(f"Camera {camera_id!r} has no local source; pass --source")
    calibration: CameraCalibration = {
        "id": camera_id,
        "K": config["K"],
        "dist": config["dist"],
        "R": config.get("R"),
        "t": config.get("t"),
    }
    return calibration, parse_source(source)


def draw_skeleton(image: np.ndarray, keypoints: list[list[float]]) -> None:
    for a, b in BONES:
        if keypoints[a][2] >= JOINT_MIN_CONF and keypoints[b][2] >= JOINT_MIN_CONF:
            pa = (round(keypoints[a][0]), round(keypoints[a][1]))
            pb = (round(keypoints[b][0]), round(keypoints[b][1]))
            cv2.line(image, pa, pb, (255, 200, 0), 2)


class PoseStats:
    """--pose sanity log every 5 s: nose height, ankle height, valid joints, fps."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id, self.t0 = camera_id, time.time()
        self.frames, self.nose, self.ankle, self.joints = 0, [], [], []

    def add(self, message: dict[str, Any]) -> None:
        self.frames += 1
        for d in message["detections"]:
            kp = d.get("kp3d")
            if not kp:
                continue
            self.joints.append(sum(j is not None for j in kp))
            if kp[0]:
                self.nose.append(kp[0][2])
            ankles = [kp[i][2] for i in (15, 16) if kp[i]]
            if ankles:
                self.ankle.append(sum(ankles) / len(ankles))
        now = time.time()
        if now - self.t0 >= 5:
            fmt = lambda v: f"{np.mean(v):.2f} m" if v else "-"  # noqa: E731
            print(f"[{self.camera_id}] pose: nose z {fmt(self.nose)} (expect 1.5-1.8), ankle z {fmt(self.ankle)} "
                  f"(expect 0.0-0.15), valid joints {np.mean(self.joints) if self.joints else 0:.1f}/17, "
                  f"skeletons {len(self.joints)}, {self.frames / (now - self.t0):.1f} fps", flush=True)
            self.__init__(self.camera_id)


def make_message(
    camera_id: str,
    calibration: CameraCalibration,
    frame: np.ndarray,
    result: FrameDetections,
    pose: dict[str, Any] | None = None,
    banner: tuple[str, float] | None = None,
) -> dict[str, Any]:
    annotated = frame.copy()
    world_detections = []
    for detection in result["detections"]:
        x, y, width, height = detection["bbox"]
        top_left = (round(x), round(y))
        bottom_right = (round(x + width), round(y + height))
        color = (0, 220, 255)
        cv2.rectangle(annotated, top_left, bottom_right, color, 2)
        cv2.putText(
            annotated,
            f"{detection['class_name']} {detection['confidence']:.2f}",
            (top_left[0], max(20, top_left[1] - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
        if calibration.get("R") is None:  # no camera position yet (auto-pose still looking for markers)
            continue
        try:
            world_detection = project_detection(detection, calibration)
            if "keypoints" in detection:  # --pose: lift the skeleton onto the person's billboard plane
                world_detection["kp3d"] = lift_keypoints(
                    detection["keypoints"], detection["bbox"], world_detection["position"], calibration
                )
                draw_skeleton(annotated, detection["keypoints"])
            world_detections.append(world_detection)
        except ValueError as error:
            print(
                f"[{camera_id}] skipped unprojectable {detection['class_name']} "
                f"detection: {error}",
                flush=True,
            )

    if banner is not None:  # camera state on the preview, so the map's feed shows it too
        state, weight = banner
        cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 34), STATE_COLOURS.get(state, (128, 128, 128)), -1)
        cv2.putText(annotated, f"{camera_id} {state} w={weight:.1f}", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 0, 0), 2)
    scale = min(1.0, MAX_PREVIEW_WIDTH / annotated.shape[1])
    if scale < 1:
        annotated = cv2.resize(
            annotated,
            (round(annotated.shape[1] * scale), round(annotated.shape[0] * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, encoded = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 60])
    if not ok:
        raise RuntimeError(f"[{camera_id}] failed to JPEG-encode camera preview")

    message = {
        "cam": camera_id,
        "t": result["timestamp"],
        "frame": result["frame"],
        "detections": world_detections,
        "jpeg": base64.b64encode(encoded).decode("ascii"),
    }
    if pose is not None:
        message["pose"] = {k: pose[k] for k in ("R", "t", "pos", "yaw", "err", "ids")}
    return message


class ServerClock:
    """Offset between this laptop's clock and the server's, from each frame's round trip (NTP-style):
    offset = server_t - midpoint(send, reply). Uses the fastest of the recent round trips (least queueing)."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []  # (round trip, offset)
        self.offset: float | None = None

    def sample(self, sent: float, received: float, server_t: Any) -> None:
        if not isinstance(server_t, (int, float)):
            return  # older server without server_t: it falls back to its own estimate
        self.samples = (self.samples + [(received - sent, float(server_t) - (sent + received) / 2)])[-50:]
        self.offset = min(self.samples)[1]


STATE_COLOURS = {"TRUSTED": (60, 200, 60), "DEGRADED": (0, 190, 255), "EXCLUDED": (0, 120, 255), "OFFLINE": (128, 128, 128)}


def offline_status(camera_id: str, reason: str) -> dict[str, Any]:
    return {"type": "status", "cam": camera_id, "t": time.time(), "state": "OFFLINE", "weight": 0.0,
            "reasons": [reason]}


async def publish(camera_id: str, source: str | int, args: argparse.Namespace, auto: AutoPose | None,
                  th: dict[str, Any]) -> str:
    """One camera session: open the camera, stream until it stops, return why. The caller reconnects.
    Hard disconnect: no new frames for > stale_timeout (or the stream ends) -> final OFFLINE status, release the
    camera, close the socket. EXCLUDED -> keep the camera and refresh, send heartbeats + preview, no detections."""
    import websockets

    calibration, _ = load_camera(camera_id, str(source))
    config = get_camera(camera_id)
    size = (config.get("width"), config.get("height"))
    warned = []
    if auto is not None:
        auto.reconnected()
        if auto.pose is not None:
            calibration["R"], calibration["t"] = auto.pose["R"], auto.pose["t"]
    elif calibration["R"] is None:
        raise ValueError(f"Camera {camera_id!r} has no saved position (R, t); run with --refresh-s > 0")
    outbound: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
    clock = ServerClock()
    stop = threading.Event()
    finished = threading.Event()
    failure: list[BaseException] = []
    loop = asyncio.get_running_loop()
    last_frame = [0.0]       # monotonic time of the latest camera frame
    last_heartbeat = [0.0]
    refresh_requested = threading.Event()

    def put_latest(message: dict[str, Any]) -> None:
        try:
            outbound.put_nowait(message)
        except queue.Full:
            try:
                outbound.get_nowait()
            except queue.Empty:
                pass
            outbound.put_nowait(message)

    pose_stats = PoseStats(camera_id) if args.pose else None

    def on_frame(frame: np.ndarray, result: FrameDetections) -> bool:
        now = time.monotonic()
        last_frame[0] = now
        if not warned and size[0] and (frame.shape[1], frame.shape[0]) != size:
            warned.append(True)
            print(f"[{camera_id}] WARNING: camera gives {frame.shape[1]}x{frame.shape[0]} but it was calibrated at "
                  f"{size[0]}x{size[1]}: floor positions will be WRONG. Wrong --source index, or another camera.",
                  flush=True)
        elif not warned:
            warned.append(True)
            print(f"[{camera_id}] camera OK at {frame.shape[1]}x{frame.shape[0]}, sending detections", flush=True)
        if auto is not None:
            if refresh_requested.is_set():
                refresh_requested.clear()
                auto.request_refresh()
            if auto.feed(frame):
                calibration["R"], calibration["t"] = auto.pose["R"], auto.pose["t"]
            state, weight, reasons = auto.state, auto.weight, auto.reasons
            if state == "OFFLINE":  # first frames after (re)connect, before the first check finishes
                state, weight, reasons = "DEGRADED", float(th["degraded_weight"]), ["checking markers"]
        else:
            state, weight, reasons = "TRUSTED", 1.0, ["auto-refresh off"]
        if state not in ("TRUSTED", "DEGRADED"):  # EXCLUDED: heartbeat + preview ~2 Hz, no detections
            if now - last_heartbeat[0] < 0.5:
                return not stop.is_set()
            last_heartbeat[0] = now
            result = {**result, "detections": []}
        message = make_message(camera_id, calibration, frame, result, auto.pose if auto is not None else None,
                               banner=(state, weight))
        message.update(state=state, weight=round(weight, 3), reasons=list(reasons)[-2:])
        if auto is not None:
            message["refresh"] = {
                "enabled": True,
                "next_in_s": max(0.0, auto.next_check - time.time()) if auto.last_check_at is not None else None,
                "last_check_at": auto.last_check_at,
                "last_log": auto.last_log,
            }
        else:
            message["refresh"] = {"enabled": False}
        if pose_stats is not None:
            pose_stats.add(message)
        loop.call_soon_threadsafe(put_latest, message)
        return not stop.is_set()

    def detect_camera() -> None:
        try:
            for _ in detect_video(
                source,
                model_path=args.model,
                confidence=args.confidence,
                classes=args.classes,
                device=args.device,
                on_frame=on_frame,
                width=size[0],
                height=size[1],
            ):
                if stop.is_set():
                    break
        except BaseException as error:
            failure.append(error)
        finally:
            finished.set()  # detect_video releases the capture on exit

    stale = float(th["stale_timeout"])
    try:
        async with websockets.connect(args.server, max_size=2_000_000) as websocket:
            print(f"[{camera_id}] connected to fusion server {args.server}", flush=True)
            thread = threading.Thread(target=detect_camera, name=f"camera-{camera_id}", daemon=True)
            thread.start()
            first_frame_deadline = time.monotonic() + 20  # opening the camera + loading the model
            reason = ""
            try:
                while True:
                    if failure:
                        reason = f"camera detection failed: {failure[0]}"
                        break
                    if finished.is_set() and outbound.empty():
                        reason = "camera stream ended (unplugged / USB hiccup)"
                        break
                    now = time.monotonic()
                    if last_frame[0] == 0.0 and now > first_frame_deadline:
                        reason = "no frames from the camera"
                        break
                    if last_frame[0] and now - last_frame[0] > stale:
                        reason = f"no new frames for {now - last_frame[0]:.1f} s"
                        break
                    try:
                        message = await asyncio.to_thread(outbound.get, True, 0.25)
                    except queue.Empty:
                        continue
                    if clock.offset is not None:  # capture time on the SERVER's clock (laptop clocks differ)
                        message["t_server"] = message["t"] + clock.offset
                    sent = time.time()
                    await websocket.send(json.dumps(message, separators=(",", ":")))
                    response = json.loads(await websocket.recv())
                    clock.sample(sent, time.time(), response.get("server_t"))
                    if response.get("ok") is not True:
                        raise RuntimeError(f"fusion server rejected frame {message.get('frame')}: "
                                           f"{response.get('error', 'unknown error')}")
                    if response.get("recalibrate") is True:
                        refresh_requested.set()
                # hard disconnect: tell the server, then release the camera and close the socket
                if auto is not None:
                    auto.set_offline(reason)
                print(f"[{camera_id}] OFFLINE: {reason}", flush=True)
                try:
                    await websocket.send(json.dumps(offline_status(camera_id, reason)))
                    await asyncio.wait_for(websocket.recv(), 1.0)
                except Exception:  # noqa: BLE001 - best effort
                    pass
                return reason
            finally:
                stop.set()
                await asyncio.to_thread(thread.join, 3)
    except (OSError, websockets.WebSocketException) as error:
        stop.set()
        if auto is not None:
            auto.set_offline(f"server connection failed: {error}")
        return f"server connection failed ({error})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("camera_id", help="Camera ID in config/cameras.json, e.g. camA")
    parser.add_argument("--source", help="Local camera index or stream URL; overrides local config")
    parser.add_argument(
        "--server",
        required=True,
        help="Fusion server ingest URL, e.g. ws://192.168.1.20:8000/ingest/camA",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="YOLO weights",
    )
    parser.add_argument("--confidence", type=float, default=0.25, help="Minimum confidence, 0 to 1")
    parser.add_argument("--classes", type=int, nargs="+", help="Optional YOLO class IDs, e.g. 0 for person")
    parser.add_argument("--device", help="Inference device, such as cpu, mps, or 0")
    parser.add_argument("--pose-interval", type=float, default=None, help="old name for --refresh-s")
    # calibration refresh + connection (defaults: config/refresh.json; see calib/refresh.py)
    for name, help_text in [("refresh_s", "seconds between marker checks while TRUSTED (0 = off: saved pose only)"),
                            ("excluded_retry_s", "re-check interval while EXCLUDED"),
                            ("verify_retry_s", "re-check interval while verifying after a reconnect"),
                            ("pixel_tol", "marker counts as moved above this mean corner error (px)"),
                            ("reproj_thresh", "accept a solve below this reprojection error (px)"),
                            ("degraded_weight", "fusion weight while DEGRADED"),
                            ("stale_timeout", "no new frames for this long -> OFFLINE (s)"),
                            ("pose_eps_m", "ignore pose changes smaller than this (m)"),
                            ("pose_eps_deg", "ignore pose changes smaller than this (deg)")]:
        parser.add_argument("--" + name.replace("_", "-"), dest=name, type=float, default=None, help=help_text)
    parser.add_argument(
        "--pose",
        action="store_true",
        help="pose model (weights/yolo11n-pose.pt): foot from the ankles, 3D skeleton (kp3d) in each detection",
    )
    args = parser.parse_args()
    if args.model is None:
        if args.pose:
            args.model = str(POSE_WEIGHTS) if POSE_WEIGHTS.is_file() else "yolo11n-pose.pt"
        else:
            args.model = str(DEFAULT_WEIGHTS) if DEFAULT_WEIGHTS.is_file() else "yolo11n.pt"
    if not 0 <= args.confidence <= 1:
        parser.error("--confidence must be between 0 and 1")

    th = load_thresholds(**{k: getattr(args, k) for k in DEFAULTS if hasattr(args, k)})
    if args.pose_interval is not None:  # old flag: same as --refresh-s
        th["refresh_s"] = args.pose_interval
    calibration, source = load_camera(args.camera_id, args.source)
    config = get_camera(args.camera_id)
    auto = None
    if th["refresh_s"] > 0:  # one AutoPose for the whole run: its pose + state survive camera reconnects
        saved = ({"R": calibration["R"], "t": calibration["t"], "pos": config.get("pos"), "yaw": config.get("yaw")}
                 if calibration["R"] is not None else None)
        auto = AutoPose(args.camera_id, calibration["K"], calibration["dist"], th,
                        log=lambda text: print(text, flush=True), initial=saved)
        print(f"[{args.camera_id}] calibration refresh on: check at (re)connect, then every {th['refresh_s']:.0f} s "
              f"(EXCLUDED: {th['excluded_retry_s']:.0f} s); " + ("stored pose from config/cameras.json"
              if saved else "no stored pose: EXCLUDED until a 4-marker solve"), flush=True)
    live = isinstance(source, int) or str(source).startswith(("http://", "https://", "rtsp://"))
    backoff = 1.0
    try:
        while True:
            started = time.monotonic()
            try:
                reason = asyncio.run(publish(args.camera_id, source, args, auto, th))
            except RuntimeError as error:
                reason = str(error)
            if not live:
                break  # a video file: done
            if time.monotonic() - started > 10:
                backoff = 1.0  # it was running fine: reconnect quickly
            print(f"[{args.camera_id}] {reason}; reconnecting camera {source!r} in {backoff:.0f} s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 5.0)
    except KeyboardInterrupt:
        print(f"\n[{args.camera_id}] stopped", flush=True)


if __name__ == "__main__":
    main()
