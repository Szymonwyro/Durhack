# DurHack 2026: seeing people through walls

Fixed cameras around a room each find people and work out where they stand on one shared floor map. A fusion server
merges those sightings into one track per person and keeps estimating where each one is when no camera can see them.
A Meta Quest 2, aligned to the same map, draws them through the wall as ghosts; a top-down map in the browser shows
everything. Pitch: shared situational awareness for firefighters and search & rescue.

Docs: [CONTRACTS](docs/CONTRACTS.md) · [PLAN](docs/PLAN.md) · [DEMO_SCRIPT](docs/DEMO_SCRIPT.md) ·
[Quest INTERFACE](quest/INTERFACE.md) · technical brief (same content as "How it works", with diagrams):
https://claude.ai/artifact/A5HWpdMg3yfzdyX343ivJJ

## Live server (now)

| Computer | Runs |
|---|---|
| Caspar's Windows PC (`10.247.206.87`) | fusion server only, port 8010 |
| Szymon's Mac (Mac-1) | camA worker (with `--pose`) |
| Jack's computer | camB worker (+ the Quest bridge) |

**The fusion server runs on Caspar's Windows PC: `http://10.247.206.87:8010/`** (port 8010).
- Map: `http://10.247.206.87:8010/`. Health: `/health`. Camera states: `/cameras`. Tracks: `ws://10.247.206.87:8010/tracks`.
- **Caspar:** server only: `python -m uvicorn fusion.server:app --host 0.0.0.0 --port 8010` on current `main`.
- **Szymon / camA:** `python -m tools.camera_worker camA --source 1 --server ws://10.247.206.87:8010/ingest/camA --classes 0 --pose`
- **Jack / camB:** `python -m tools.camera_worker camB --source N --server ws://10.247.206.87:8010/ingest/camB --classes 0 --pose`
  (`--pose` = skeletons; see "Skeletons on this camera" in docs/HANDOFF_CAMC_JACK.md).
- **Jack / Quest:** `git pull`, then on the machine the Quest is plugged into:
  `python3 quest/tools/bridge.py` and `python3 tools/fusion_to_quest.py ws://10.247.206.87:8010/tracks --margin 0.2`
  (bridge needs `pip install aiohttp websockets`). The Quest then opens `http://localhost:8001/quest/`.
  `--margin 0.2` hides bystanders next to the square; keep others out of the square.
- **Quest alignment:** touch the 4 corner markers in the order the headset prompts: **1 → 7 → 3 → 5** (round the
  square). Wrong order = boxes move in the wrong direction.

## How it works

```
calibrate (once)      camera laptop                     fusion server                 viewers
chessboard → lens  →  YOLO11n-pose → foot pixel     →   match + Kalman per person  →  Quest (through walls)
floor markers → pose  → ray hits floor (x, y)            visible / predicted           top-down map
                      → 17 joints lifted to 3D           skeleton fusion
```

### World frame
Four ArUco markers (IDs 1, 3, 5, 7, DICT_4X4_50, 180 mm) lie flat on the floor at the corners of a 1.5 m × 1.5 m
square: ID 1 = origin (0, 0), 7 = (1.5, 0), 5 = (0, 1.5), 3 = (1.5, 1.5). Metres; x, y on the floor, z up. Every
camera and the headset register to these same markers, which is why a person seen by camA lands at the right spot
in the headset.

### Calibration (`calib/`)
- **Lens (intrinsics):** `python -m calib.intrinsics <cam> --source N`. A 9×6 chessboard (25 mm) waved around the
  image; OpenCV `calibrateCamera` fits K (focal lengths, principal point) and 5 distortion coefficients.
  camA: 0.46 px RMS at 1280×720, 61.7° horizontal FOV. Aim for < 0.5 px.
- **Position (extrinsics):** `python -m calib.position <cam>`. Perspective-n-Point from the floor markers: with all 4
  in view it uses the marker centres (SQPNP), with 1–3 it uses every corner (IPPE, needs each sheet's measured
  rotation), then Levenberg–Marquardt refinement. Quality = reprojection error. camA now: 3.0 px, camera at
  (−1.61, −2.31) m, 2.57 m high, yaw 51°, tilted down 26°.
- **Live self-check** (`calib/auto_pose.py`, `calib/refresh.py`): on connect and every 60 s the worker compares where
  the markers should be with where they are. Most markers moved → the camera was bumped → re-solve (accepted under
  4.5 px). A minority moved → a marker was kicked → keep the pose. Changes under 1 cm / 0.5° are ignored.

### Sensing (`perception/`, `tools/camera_worker.py`)
1. **Detect.** YOLO on every frame, people only (`--classes 0`), confidence ≥ 0.25. With `--pose` the model is
   **YOLO11n-pose** (`weights/yolo11n-pose.pt`, Ultralytics nano): box + 17 COCO keypoints in one pass; without it,
   `yolo11n.pt` (boxes). camA runs about 15 fps.
2. **Foot pixel.** Midpoint of the ankles if both have confidence > 0.4, else the bottom-centre of the box.
3. **Pixel → floor.** Undistort the pixel, turn it into a world ray (Rᵀ·[u, v, 1]) from the camera centre C = −Rᵀt,
   and intersect it with the floor z = 0 (`perception/floor_project.py: pixel_to_floor`). Flat-floor assumption: one
   camera is enough to place a person in x, y. The foot pixel is the main error source, so cameras go high and tilt down.
4. **3D skeleton ("billboard" lifting,** `perception/pose.py`**).** The body is treated as a vertical plane through the
   foot point, facing the camera; each joint's ray is intersected with that plane. Height and sideways position are
   accurate, depth along the view is about ±0.2 m. Needs joint confidence ≥ 0.3, box ≥ 120 px tall, ≥ 8 valid joints.
   The worker logs a sanity check every 5 s: nose 1.5–1.8 m, ankles 0.0–0.15 m for a standing person.

### Transport and clocks
One WebSocket per camera to `/ingest/<cam>`, one JSON message per frame: world detections (+ `kp3d`), a small JPEG
preview for the map, and the camera's current pose. If a worker's clock agrees with the server's (median gap < 1 s)
its capture times are used as-is; otherwise the server assumes the fastest recent frame had zero delay. Lag per camera
is in `GET /cameras` (camB ~62 ms over Wi-Fi).

### Camera states (`fusion/cameras.py`)
| State | Meaning | Effect |
|---|---|---|
| TRUSTED (green) | markers where the pose predicts | detections used, weight 1 |
| DEGRADED (amber) | reconnected with no markers in view, or a poor re-solve | detections used; weight 0.4 in skeleton fusion |
| EXCLUDED (orange) | markers say the camera moved | detections dropped, re-checked every 5 s |
| OFFLINE (grey) | socket closed, unplugged, or silent 2.5 s | detections dropped, people only it saw go predicted |

The 0.4 weight affects skeleton fusion only; the position Kalman treats TRUSTED and DEGRADED detections the same.

### Tracking and the Kalman filter (`fusion/tracker.py`)
Per camera frame: **dedupe → associate → update**; tracks go out to clients about 10× a second.
- **Dedupe:** one camera can't see one person twice; detections in one frame closer than 0.4 m are merged.
- **Associate:** cost = distance from each track (moved to the frame's capture time) to each detection, allowed only
  inside a gate of 1.0 m + 2σ of the track's uncertainty; the Hungarian algorithm picks the one-to-one matching.
  Unmatched detections start new tracks. All cameras report in world metres, so matching across cameras is the same
  step as matching over time.
- **Kalman (constant velocity):** state [px, py, vx, vy]; process noise = random acceleration σ 0.8 m/s²;
  we measure position only, σ 0.10 m (+0.5 m per second of lag); a new track starts at σ 0.3 m, velocity 0 ± 1 m/s.
  Velocity is never measured directly: the filter infers it, and that is what keeps a person moving after the
  cameras lose them. A late frame is shifted forward along the track's velocity (≤ 1 s) and trusted a bit less
  instead of dragging the person back.
  ```
  predict:  x ← F x              P ← F P Fᵀ + Q
  update:   K = P Hᵀ (H P Hᵀ + R)⁻¹     x ← x + K (z − H x)     P ← (I − K H) P
  ```
- **Lifecycle:** shown after 3 hits · `visible` if seen ≤ 0.5 s ago · `predicted` keeps moving at its last velocity
  for ≤ 1.5 s, then holds · uncertainty grows 0.35 m/s · dropped after 8 s or when 2σ > 3 m (≈ 4 s in practice) ·
  glimpses (< 10 hits) dropped after 1 s · a predicted track within 0.6 m of a visible one is merged (older id kept).
- A camera going OFFLINE/EXCLUDED is removed at once; people only it saw turn `predicted`, like walking behind a wall.

### Multi-camera skeleton fusion (`fusion/skeleton.py`)
A lifted joint is precise across its camera's view and wrong only along the camera ray, so two cameras at different
angles pin it down. Per track: joints taken relative to each camera's own foot point and re-anchored at the fused
Kalman position (calibration offsets cancel) → ray-shaped covariance (4 cm across, 40 cm along the ray, scaled by
1 / (keypoint conf × camera weight × 0.5 s ramp-in)) → left/right swap check → outlier gate (Mahalanobis > 3) →
information-form fusion + EMA (α 0.5); a missing joint is held 0.5 s. No camera left: the skeleton freezes, moves with
the Kalman prediction, and goes after 3 s. With one camera this reduces to that camera's smoothed skeleton.

### Quest viewer (`quest/`)
```
fusion /tracks → tools/fusion_to_quest.py → quest/tools/bridge.py (:8001) → adb reverse → Quest browser (WebXR)
```
- The viewer accepts only the `quest/INTERFACE.md` format, which the server doesn't speak yet, so
  `fusion_to_quest.py` forwards each **visible** person (and skeleton) into the bridge. Predicted tracks are not
  forwarded: in the headset, prediction comes from the bridge's lighter alpha-beta filter (α 0.5, β 0.15, speed ≤ 3 m/s,
  velocity decays over ~1.5 s, σ grows 0.35 m/s). The full Kalman drives the top-down map.
- **Alignment** (`quest/src/align.js`): touch markers 1 → 7 → 3 → 5 with the controller; least-squares 2D rigid fit
  (yaw + x/y). A marker > 8 cm off is flagged for redo. The headset never sends its pose anywhere.
- **Hidden vs visible** is decided in the headset (`quest/src/los.js`): a 2D line from the wearer's head to the
  person; if it crosses a wall segment the person is drawn orange and on top of everything, otherwise cyan. Predicted
  people are dim and pulsing. Quest 2 passthrough is greyscale, hence cyan/orange only.

### Built vs planned
| Piece | Status |
|---|---|
| Calibration + live self-check, camera states | working |
| Detection, floor projection, skeleton lifting | working (camA ~15 fps) |
| Server tracker + Kalman, map | working |
| Multi-camera skeleton fusion | built and tested; needs two cameras online to show |
| Quest via bridge + fusion_to_quest | working |
| Walls | **missing:** no `config/walls.json` yet (copy `walls.example.json`, measure the real wall). Without it the map has no walls and the bridge uses two placeholder walls |
| UNO Q camera, PIR, LED radar | not in the repo (`edge/` is empty) |
| Server speaking INTERFACE.md directly | not done; the bridge covers it |
| iPhone fallback viewer (`web/viewer/`) | placeholder page only |

## Setup
```bash
source .venv/bin/activate              # Python 3.11
# re-create elsewhere: uv venv --python 3.11 .venv && uv pip install -r requirements.txt
#   then: uv pip uninstall opencv-python opencv-contrib-python && uv pip install opencv-contrib-python
#   (ultralytics pulls in plain opencv-python, which clobbers cv2.aruco)
cp .env.example .env
```
The torch device on a Mac is **mps** (Apple Silicon). Weights live in `weights/` (git-ignored; `*.pt` too). If
`weights/yolo11n-pose.pt` is missing, Ultralytics downloads it (~6 MB) into the current folder on first run.

## Tests and tools
```bash
python tests/test_tracker.py           # also test_cameras, test_pose, test_refresh, test_skeleton_fusion
python tests/smoke_boxes.py            # end to end, boxes only: server + replay + real worker
python tools/test_walk.py replay samples/test_walk2_camA_pose.jsonl --fusion --url ws://<server>/ingest/camA
python tools/check_env.py              # imports + versions + torch device
python tools/lan_ip.py                 # this computer's LAN IP
python tools/list_cameras.py           # camera indices 0-5
python tools/test_aruco.py 0 30        # marker IDs seen by camera 0 for 30 s
python tools/net_check.py https://<ip>:8000  # DNS / TCP / GET /world / WS /tracks
tools/tunnel.sh                        # fallback: public cloudflared URL -> config/server.json
tools/quest_usb.sh [--wireless]        # Quest from Mac-1: adb reverse -> http://localhost:8001/quest/
tools/iphone_cert_help.sh              # how iPhones trust the server cert (mkcert CA / tunnel)
```
On macOS, grant camera access to your terminal app first: **System Settings → Privacy & Security → Camera**, then
restart the terminal.

## Shared live map (multiple computers)
One computer runs the fusion server; every computer has the same calibrated `config/cameras.json`. `source` is local
to each computer, so pass the local camera index with `--source`.
```powershell
python -m uvicorn fusion.server:app --host 0.0.0.0 --port 8010                                     # server
python -m tools.camera_worker camA --source 1 --server ws://10.247.206.87:8010/ingest/camA --classes 0 --pose  # each camera
```
Find the server's LAN IP with `python tools/lan_ip.py` and allow Python/Uvicorn through the firewall on the private
network if prompted. Open `http://<server>:8010/` on any computer for the shared map and camera feeds. Keep cameras
fixed after calibration, and use a network that allows device-to-device traffic.

## HTTPS for iPhones (and the Quest)
Mobile browsers only allow `getUserMedia` (camera) over HTTPS, except on localhost.

**Re-run `tools/make_certs.sh` on every new network.** The certs include the LAN IP.

- Recommended: `brew install mkcert nss`, then `tools/make_certs.sh` → `certs/cert.pem`, `certs/key.pem`.
- Fallback: `certs/selfsigned.pem`, `certs/selfsigned-key.pem`.

```bash
uvicorn fusion.server:app --host 0.0.0.0 --port 8000 --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
```
The Quest doesn't need a cert: over USB it uses `http://localhost:8001` via `adb reverse` (`tools/quest_usb.sh`),
which counts as a secure context. Venue Wi-Fi often blocks device-to-device traffic, so check with
`tools/net_check.py`; if it fails, use an iPhone Personal Hotspot or `tools/tunnel.sh`. See Networking in CLAUDE.md.

Accepting the cert:
- **Self-signed, iPhone/Safari:** you can tap through the warning, but wss:// WebSockets may still fail. Don't rely on it.
- **Tunnel (`tools/tunnel.sh`):** a real cert, nothing to install.
- **mkcert:** `tools/iphone_cert_help.sh` covers AirDropping `$(mkcert -CAROOT)/rootCA.pem` to the iPhone, installing
  the profile, and enabling it under Settings → General → About → Certificate Trust Settings.
- WebSockets must use `wss://` on the same host:port as the page.

## Printing
`printables/markers.pdf` (ArUco DICT_4X4_50, IDs 0-9, 180 mm; we use only **1, 3, 5, 7**) and
`printables/chessboard.pdf` (9x6 inner corners, 25 mm). Regenerate with `python tools/make_printables.py`.
- Print on A4 at **100% / "Actual size"**; turn **off** "Fit to page". Check the **100 mm scale bar** (± 1 mm).
- Mount on stiff card and keep the white margin. Matte paper reduces glare.
- Corner markers lie flat at the corners of the 1.5 m square (see World frame). `calib/extrinsics.py` measures each
  sheet's rotation, so they can be laid any way round.

## Layout
```
calib/       lens + position calibration, live self-check (intrinsics, position, extrinsics, auto_pose, refresh)
perception/  YOLO detection, pixel -> floor projection, skeleton lifting (detect, floor_project, pose)
fusion/      FastAPI server, camera states, tracker + Kalman, skeleton fusion (server, cameras, tracker, skeleton)
quest/       WebXR viewer (src/), bridge + console (tools/), INTERFACE.md
web/map/     top-down command map (served by the fusion server)   web/viewer/  iPhone fallback (placeholder)
tools/       camera_worker, fusion_to_quest, test_walk, network/cert/Quest helpers, older single-machine map tools
tests/       unit tests + smoke_boxes.py     samples/  recorded test walks (no camera needed)
config/      cameras.json, markers.json, refresh.json, server.json, *.example.json
printables/  PDFs     weights/  recordings/  certs/  (git-ignored)
```
