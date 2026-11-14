> **OUTDATED:** the relay isn't used any more. Use [HANDOFF_LAPTOP2_SERVER_CAMB.md](HANDOFF_LAPTOP2_SERVER_CAMB.md).

# Handoff: start the camB stream on the Windows laptop (for Worker 2 / their AI assistant)

**Goal:** send the USB camera on this Windows laptop to Mac-1 (Szymon's Mac) as **camB**.
Mac-1 already runs a relay that receives the frames. Detection, calibration and the map all run on Mac-1.
This laptop only captures and pushes frames. It's an outbound connection only, so **no Windows
firewall changes are needed**.

## Rules for the AI on this machine
- Only run the commands below. **Do not hand-edit** `config/cameras.json`, `config/markers.json` or anything
  in `calib/`. The calibration scripts update camB's entry themselves.
- Never set camB's `source` to a local index: on Mac-1 it must stay the relay URL.
- Only commit `config/cameras.json`, via the calibration script's push prompt. No recordings, weights or venv in git.
- The stream must **keep running**. Start it as a long-running/background process and don't stop it
  when your task ends.
- Shell is **PowerShell**. Paths use `\`.

## Steps
1. **Get the code** (repo: https://github.com/Szymonwyro/Durhack):
   ```powershell
   cd $HOME\Desktop
   if (Test-Path Durhack) { cd Durhack; git pull } else { git clone https://github.com/Szymonwyro/Durhack.git; cd Durhack }
   ```
2. **Python environment** (one-time). Only OpenCV + NumPy are needed here:
   ```powershell
   py -3 -m venv .venv
   .venv\Scripts\pip install opencv-python numpy
   ```
   If `py` isn't found, use `python`. Any Python 3.9+ works.
3. **Find the USB camera index:**
   ```powershell
   .venv\Scripts\python tools\list_cameras.py
   ```
   Pick the USB camera, not the laptop's built-in one (usually index 0). If unsure, ask Worker 2 to cover
   the USB camera and run it again: the one that changes is the USB camera.
   If nothing opens: Windows Settings → Privacy & security → Camera → allow desktop apps.
4. **Start pushing to Mac-1** (replace `<N>` with the index from step 3):
   ```powershell
   .venv\Scripts\python tools\stream_test.py push http://10.247.46.145:8095 --cam camB --source <N>
   ```
   - **Success:** about every 5 s it prints `~30 fps sent, ~200 KB/frame`. Lower fps (15+) is still fine.
   - Mac-1 is at `10.247.46.145` on the venue Wi-Fi. If that fails, ask Szymon for Mac-1's current IP
     (on Mac-1: `python tools/lan_ip.py`).

## Calibrate camB (on this laptop, camera plugged in here, `<N>` from step 3)
Stop the push (Ctrl+C) first: only one program can use the camera at a time.
1. **Lens (chessboard), once per camera:** `.venv\Scripts\python -m calib.intrinsics camB --source <N>`
   Move the 9x6 chessboard over the whole image, including the corners, tilted. Aim for RMS < 0.5 px.
2. **Position (corner markers):** mount and tape the camera first, then
   `.venv\Scripts\python -m calib.position camB --source <N>`
   Wait for "GOOD - press S" (magenta outlines sit on the markers), then press S and answer Y to commit + push.
3. Restart the push (step 4). Then tell Szymon "camB calibrated + streaming".
If the camera moves: redo step 2 only.

## Check / troubleshoot
| Output | Meaning / fix |
|---|---|
| `~30 fps sent` | Working. Tell Szymon "camB is streaming" and leave it running. |
| `push failed (...); retrying...` | Can't reach Mac-1. Check both are on the **same Wi-Fi**, the relay is running on Mac-1 (`python tools/stream_test.py relay`), and the IP is right. It reconnects by itself. |
| `camera <N> unavailable` | Wrong index, camera in use by another app (close Teams/Zoom/Camera app), or camera privacy setting. |
| `WARNING: camera gives 640x480, not 1280x720` | Tell Szymon: camB's calibration assumes 1280x720. Try the other camera index, or a different USB port. |
| Very low fps (<10) | Use `--quality 75`, or move closer to the router. |

## Keep it running
- Plugged in, lid open. Settings → System → Power → **Screen and sleep: Never** (while plugged in).
- **Don't move or bump the camera** once mounted. Mac-1 calibrates its position in place. If it moves,
  re-run `calib.position camB` (step 2 above).
- To stop: Ctrl+C in that terminal. To restart: run the step 4 command again.

## What happens on Mac-1 (for context, nothing to do here)
- The relay re-serves the frames at `http://127.0.0.1:8095/camB/stream.mjpg` (camB's `source` in `config/cameras.json`).
- Szymon pulls the calibration and runs `python -m tools.live_map` (camA + camB on one map).
