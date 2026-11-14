# Handoff: camB on laptop 2 (Caspar) → server on Mac-1 (for Caspar / his AI)

**Setup now:** the fusion server runs on **this Windows PC at `10.247.206.87:8010`**.
Laptop 2 runs **only the camB worker** and sends to this PC. No server on the worker computer.
- Map (any browser on the Wi-Fi): `http://10.247.206.87:8010/`  ·  health: `http://10.247.206.87:8010/health`
- camB sends to: **`ws://10.247.206.87:8010/ingest/camB`**

**Server changes Caspar should know about (Role 3):** `fusion/tracker.py` is now implemented (stable ids, Kalman,
visible/predicted, optional skeletons) and `fusion/server.py` feeds every camera frame into it; `/tracks` people come
from the tracker, other classes are grouped as before (ids ≥ 100000). Workers also send their auto-calibrated `pose`,
which updates the map's camera positions. Tests: `python tests/test_tracker.py`.

## Rules for the AI
- **Camera indices are per laptop.** camB has no `source` in config/cameras.json on purpose. **Always pass `--source <N>`**,
  where N is camB's index **on this laptop**. Don't put a local index into cameras.json (camA's `source` is Mac-1's).
- Don't hand-edit config/cameras.json or config/markers.json. `calib.position` / `calib.intrinsics` update camB's entry.
- Only one program can use a camera at a time. Stop the camB worker before running calibration, and the other way round.
- Commit only config/cameras.json (via calib.position's push prompt) and code fixes you're asked for.

## 1. Update + install (once)
```powershell
git pull
.venv\Scripts\pip install opencv-contrib-python ultralytics websockets scipy
```
(macOS: `.venv/bin/...` instead of `.venv\Scripts\...`, and `/` instead of `\`.) Don't install plain `opencv-python`
next to contrib. If both are installed: `pip uninstall -y opencv-python opencv-contrib-python` then reinstall contrib.

## 2. Find camB's index on this laptop
```powershell
.venv\Scripts\python -m calib.position camB
```
It opens a window with **every camera side by side, each labelled with its index and resolution**. Note camB's number (N).
The built-in webcam is usually 0 and the USB camera 1, but check. Then press Q, or carry on to step 3 in the same window.
If camB isn't in the grid at all, see Troubleshooting.

## 3. camB position (needed: the saved one is old, from 640x480 before the lens calibration)
camB must be mounted and **taped down**, seeing as many of corner markers 1, 3, 5, 7 as possible.
```powershell
.venv\Scripts\python -m calib.position camB --source N
```
Wait for **"GOOD - press S"** (magenta outlines on the markers), press **S**, and answer **Y** to commit + push.
Then tell Szymon, so Mac-1 can `git pull` (both laptops must have the same cameras.json).
If it stops with "camera gives 640x480 but K is for 1280x720", see Troubleshooting.

## 4. Run the camB worker (leave it running)
```powershell
.venv\Scripts\python -m tools.camera_worker camB --source N --server ws://10.247.206.87:8010/ingest/camB --classes 0
```
**Success:** the worker prints `connected to fusion server` and then **`camera OK at 1280x720, sending detections`**.
**Auto-position (on by default):** the worker solves camB's position from the corner markers at startup and every 60 s
(`auto-pose: calibrated from markers [1, 3, 5, 7] ...`) and sends it to the server, so the map shows the live position.
Only a view of **all 4** markers updates it. Partial views only warn if the camera seems to have moved. Until the first
4-marker view it uses the saved position. `--pose-interval 0` turns it off. Step 3 is still worth doing once
(it saves a good starting position to git).
Check: `http://10.247.206.87:8010/health` shows `"camB":{"online":true}`, and camB's feed appears on the map page.
(Add `--device 0` on an NVIDIA GPU, `--device mps` on a Mac. CPU is the default and should still manage several fps.)

## Troubleshooting
| Symptom | Fix |
|---|---|
| `has no local source; pass --source` | Add `--source N` (step 2). |
| `Could not open video source: N` / black tile | Wrong N, or the camera is busy: close Teams/Zoom/Camera app/OBS and any other worker or calibration window. Windows: Settings → Privacy & security → Camera → allow desktop apps. Unplug and replug, ideally into a different USB port (not a hub), then redo step 2 (indices can change). |
| `WARNING: camera gives 640x480 but it was calibrated at 1280x720` | The worker now asks for 1280x720 (DirectShow + MJPG on Windows). If it still gets 640x480: it's the wrong camera (the built-in webcam), or a USB 2 hub/port is limiting it, so plug straight into the laptop. As a last resort, redo the chessboard at what it gives: `python -m calib.intrinsics camB --source N --width 640 --height 480`, then step 3 again. |
| Worker exits with `camera detection failed` | Read the error under it. Usually the camera dropped. Restart the worker. |
| `server connection failed; retrying` | The server isn't reachable. Check both computers are on the same Wi-Fi (`ipconfig`: 10.247.x.x), open `http://10.247.206.87:8010/health` in a browser here, and check the URL is `ws://10.247.206.87:8010/ingest/camB` (port **8010**). If the server computer's IP changed, check it with `python tools/lan_ip.py` on that computer. |
| Very slow (<3 fps) | `--device 0` if there's an NVIDIA GPU. Close the map tab on this laptop. Use `--classes 0` (people only). |
| camB dots in the wrong place on the map | Step 3 wasn't done or wasn't pulled on both laptops, or the camera moved. Redo step 3, then restart the camB worker (and ask Szymon to `git pull` + restart camA's worker). |

## Keep it running
Plugged in, lid open, sleep off. **Don't touch camB once it's calibrated.** If it moves, redo step 3.
