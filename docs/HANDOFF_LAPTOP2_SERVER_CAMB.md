# Handoff: laptop 2 = fusion server + camB (for laptop 2's person / their AI)

Setup now: **laptop 2** runs the fusion server (`fusion.server`, port 8000) and the camB worker.
**Mac-1** (10.247.46.145) runs the camA worker and sends to laptop 2. The map is at `http://<laptop2-ip>:8000/`.
The server works. These steps get **camB** running. (This replaces HANDOFF_CAMB_WINDOWS.md, because the relay isn't used any more.)

## Rules for the AI
- **Camera indices are per laptop.** camB has no `source` in config/cameras.json on purpose. **Always pass `--source <N>`**,
  where N is camB's index **on this laptop**. Don't put a local index into cameras.json (camA's `source` is Mac-1's).
- Don't hand-edit config/cameras.json or config/markers.json. `calib.position` / `calib.intrinsics` update camB's entry.
- Only one program can use a camera at a time. Stop the camB worker before running calibration, and the other way round.
- Commit only config/cameras.json (via calib.position's push prompt) and code fixes you're asked for.

## 1. Update + install (once)
```powershell
git pull
.venv\Scripts\pip install opencv-contrib-python ultralytics websockets fastapi "uvicorn[standard]"
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

## 4. Run (two terminals, leave both running)
```powershell
# terminal 1: server
.venv\Scripts\python -m uvicorn fusion.server:app --host 0.0.0.0 --port 8000
# terminal 2: camB worker
.venv\Scripts\python -m tools.camera_worker camB --source N --server ws://127.0.0.1:8000/ingest/camB --classes 0
```
**Success:** the worker prints `connected to fusion server` and then **`camera OK at 1280x720, sending detections`**.
**Auto-position (on by default):** the worker solves camB's position from the corner markers at startup and every 60 s
(`auto-pose: calibrated from markers [1, 3, 5, 7] ...`) and sends it to the server, so the map shows the live position.
Only a view of **all 4** markers updates it. Partial views only warn if the camera seems to have moved. Until the first
4-marker view it uses the saved position. `--pose-interval 0` turns it off. Step 3 is still worth doing once
(it saves a good starting position to git).
Check: `http://127.0.0.1:8000/health` shows `"camB":{"online":true}`, and camB's feed appears on the map page.
(Add `--device 0` on an NVIDIA GPU, `--device mps` on a Mac. CPU is the default and should still manage several fps.)

## Troubleshooting
| Symptom | Fix |
|---|---|
| `has no local source; pass --source` | Add `--source N` (step 2). |
| `Could not open video source: N` / black tile | Wrong N, or the camera is busy: close Teams/Zoom/Camera app/OBS and any other worker or calibration window. Windows: Settings → Privacy & security → Camera → allow desktop apps. Unplug and replug, ideally into a different USB port (not a hub), then redo step 2 (indices can change). |
| `WARNING: camera gives 640x480 but it was calibrated at 1280x720` | The worker now asks for 1280x720 (DirectShow + MJPG on Windows). If it still gets 640x480: it's the wrong camera (the built-in webcam), or a USB 2 hub/port is limiting it, so plug straight into the laptop. As a last resort, redo the chessboard at what it gives: `python -m calib.intrinsics camB --source N --width 640 --height 480`, then step 3 again. |
| Worker exits with `camera detection failed` | Read the error under it. Usually the camera dropped. Restart the worker. |
| `server connection failed; retrying` | The server (terminal 1) isn't running, or the URL is wrong. On this laptop use `ws://127.0.0.1:8000/ingest/camB`. |
| Very slow (<3 fps) | `--device 0` if there's an NVIDIA GPU. Close the map tab on this laptop. Use `--classes 0` (people only). |
| Mac-1's camA shows offline | Windows firewall: allow Python on **Private** networks (or `New-NetFirewallRule -DisplayName durhack8000 -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow` in admin PowerShell). The network profile must be Private, not Public. |
| camB dots in the wrong place on the map | Step 3 wasn't done or wasn't pulled on both laptops, or the camera moved. Redo step 3, then restart both workers. |

## Keep it running
Plugged in, lid open, sleep off. **Don't touch camB once it's calibrated.** If it moves, redo step 3.
