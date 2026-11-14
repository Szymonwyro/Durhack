# Handoff: Jack's computer runs a camera worker (for Jack / his AI)

> **Now: Jack's computer runs camB** (already calibrated and feeding Caspar's server at `10.247.206.87:8010`).
> Caspar's PC runs the server only. To add skeletons, go straight to **"Skeletons on this camera"** below with
> `<camId>` = `camB` and `<server>` = `10.247.206.87`. The steps for a brand-new camera (camC) stay below for reference.

**Goal:** a camera plugged into Jack's Mac becomes **camC** and sends detections to the fusion server on Caspar's laptop
(`ws://10.247.206.87:8010`, see README "Live server"). camA (Szymon's Mac) and camB (Caspar) are already running.
This runs alongside Jack's Quest bridge, and doesn't change it.

## Rules for the AI
- Only Jack's own camC entry in `config/cameras.json` changes, and only via the scripts below. Don't hand-edit it or
  `config/markers.json`.
- **Don't commit or push after step 3 alone.** The server refuses to start if a camera has a lens calibration but no
  position. Push once, at the end of step 4 (the script's Y prompt does that).
- Camera numbers are per computer and change when devices are (un)plugged: always pass `--source N`.
- One program per camera: close previews before running the next step.

## 1. Update + install (once, in the repo)
```bash
git pull
python3 -m venv .venv            # if there's no .venv yet (Python 3.10+)
.venv/bin/pip install opencv-contrib-python ultralytics websockets scipy
```
Don't install plain `opencv-python` alongside contrib. If both are installed, uninstall both, then reinstall contrib.
macOS: allow the terminal to use the camera (System Settings → Privacy & Security → Camera), then restart the terminal.

## 2. Find the camera's number (N)
```bash
.venv/bin/python -m calib.position camC
```
It shows **every camera side by side**, each labelled with its number: press the number of camC's camera.
On a new camera it then stops and prints the exact step-3 command with that number filled in. That's expected.
If it's an **iPhone via Continuity Camera:** it must be Jack's own iPhone (same Apple ID as the Mac). Mount it in
landscape, locked and charging. In Control Centre → **Video Effects**, turn **off Center Stage**, Portrait, Studio
Light and Reactions (Center Stage crops and pans the image, which breaks calibration). Use the 1x lens and never zoom.
Continuity Camera's 1x is narrow (~46°), so it needs to be about 2–2.5 m from the square.

## 3. Lens calibration (chessboard, once per camera + resolution)
```bash
.venv/bin/python -m calib.intrinsics camC --source N --width 1280 --height 720
```
Show the 9x6 chessboard (printed, or full-screen on another screen: the square size doesn't matter) all over the
image, including the corners, near and far, tilted up to ~45°. It auto-captures 20 views. Aim for **RMS < 0.5 px**.
**Don't commit yet.**

## 4. Position (corner markers), with the camera mounted for good
Mount it so it sees **all 4 corner markers (1, 3, 5, 7)**, with room above the square for people head to toe.
Ideally it covers what camA and camB miss. Then:
```bash
.venv/bin/python -m calib.position camC --source N
```
Wait for **"GOOD - press S"**, press **S**, and answer **Y** to commit + push (this pushes steps 3 + 4 together).
Then tell **Caspar to `git pull` and restart his server**. It only learns about new cameras at startup;
camA and camB reconnect by themselves.

## 5. Run the camC worker (leave it running)
```bash
.venv/bin/python -m tools.camera_worker camC --source N --server ws://10.247.206.87:8010/ingest/camC --classes 0 --device mps
```
(`--device mps` on Apple Silicon. Leave it out on Intel Macs.)
**Success:** `connected to fusion server`, then `camera OK at 1280x720, sending detections`, then every 60 s an
`auto-pose: ...` line. Check `http://10.247.206.87:8010/health`: `"camC": {"online": true, "fps": ..., "lag_ms": ...,
"clock": "synced"}`.

## Skeletons on this camera (`--pose`)
Same worker, one extra flag. It draws the skeleton on this camera's feed and sends 3D joints to the server, so the
map (tick **Skeletons**) and `/tracks` carry a skeleton for everyone this camera sees.
1. `git pull` on `main` (on `feat/boxes`: `git pull --rebase origin main` first).
2. Stop the running worker for this camera (Ctrl+C), then start it again with `--pose` added:
   ```
   python -m tools.camera_worker <camId> --source N --server ws://<server>:8010/ingest/<camId> --classes 0 --pose
   ```
   `<camId>` = this laptop's camera (e.g. `camB`), `N` = its camera number here, `<server>` = `127.0.0.1` if the
   fusion server runs on this laptop, otherwise the address in README "Live server". Add `--device mps` on an Apple
   Silicon Mac or `--device 0` with an NVIDIA GPU. Windows: `.venv\Scripts\python`; macOS: `.venv/bin/python`.
3. The first run downloads the pose model (`yolo11n-pose.pt`, ~6 MB; needs internet once).
4. **Check the log.** Every 5 s: `pose: nose z 1.5-1.8 m, ankle z 0.0-0.15 m, valid joints 12+/17, ... fps`.
   - ankles near 0 = working. Nose low or `-` = heads are out of frame: tilt the camera up so people fit head to toe.
   - `skeletons 0` = people too small (< 120 px tall) or fewer than 8 joints visible: move/aim the camera.
   - a `refresh:` line must end in `TRUSTED` (or `DEGRADED`); an `EXCLUDED` camera sends no skeletons.
5. Check `http://<server>:8010/`: this camera's feed tile shows bones on each person. Aim for ≥ 10 fps.
The server needs no change (it must be on current `main`). Without `--pose` everything behaves as before.

## Troubleshooting
| Symptom | Fix |
|---|---|
| Worker closes with code 1008 / "Unknown camera 'camC'" | Caspar's server was started before camC was pushed: he needs `git pull` + restart. |
| `WARNING: camera gives 1920x1080 / 640x480 but it was calibrated at 1280x720` | Wrong camera, or it can't do 720p. Redo step 3 at what it gives (`--width/--height`), then step 4. |
| `server connection failed; retrying` | Not on the same Wi-Fi as Caspar (`10.247.x.x`), or wrong address/port (**8010**). Open `http://10.247.206.87:8010/health` in a browser. |
| `auto-pose: CAMERA MOVED` / view jumped | The camera got bumped. Re-mount it, then redo step 4. |
| Continuity Camera freezes / disappears | The phone got unlocked, moved away or went to sleep. Lock it, keep it close and charging, then re-check N (step 2), because numbers shift. |
| Very slow | Close other camera apps and previews. The Quest bridge is light; YOLO is the heavy part. |
