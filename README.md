# DurHack prep: multi-camera situational awareness

> **Prep only.** This repo holds the environment, model weights, printables, empty stubs, config
> templates, smoke tests and docs. All project logic (detection, tracking, fusion, calibration,
> server endpoints, frontends) is written **during the event**, as the hackathon rules require.

Docs: [CONTRACTS](docs/CONTRACTS.md) · [PLAN](docs/PLAN.md) · [DEMO_SCRIPT](docs/DEMO_SCRIPT.md)

IP : http://10.247.206.87:8000/

## Setup
```bash
source .venv/bin/activate              # Python 3.11 (not 3.13: mediapipe)
# re-create elsewhere: uv venv --python 3.11 .venv && uv pip install -r requirements.txt
#   then: uv pip uninstall opencv-python opencv-contrib-python && uv pip install opencv-contrib-python
#   (ultralytics pulls in plain opencv-python, which clobbers cv2.aruco)
cp .env.example .env                   # fill ELEVENLABS_API_KEY / GEMINI_API_KEY
```
The torch device on this Mac is **mps** (Apple Silicon). Weights live in `weights/` (yolo11n, -seg, -pose, pose_landmarker_full.task).

## Smoke tests
```bash
python tools/check_env.py      # imports + versions + torch device
python tools/lan_ip.py         # IP for phones
python tools/list_cameras.py   # camera indices 0-5
python tools/test_yolo.py      # yolo11n on camera 0 (or a sample image)
python tools/test_aruco.py 0 30  # print marker IDs seen by cam 0 for 30 s
python tools/net_check.py https://<ip>:8000  # DNS / TCP / GET /world / WS /tracks
tools/tunnel.sh                # fallback: public cloudflared URL -> config/server.json
tools/quest_usb.sh [--wireless] # Quest from Mac-1: adb reverse -> http://localhost:8001/quest/
tools/iphone_cert_help.sh      # how iPhones trust the server cert (mkcert CA / tunnel)
```
On macOS, grant camera access to your terminal app first: **System Settings → Privacy & Security → Camera**. Then restart the terminal.

## Shared live map (multiple computers)
Use one computer as the fusion server. Put the same calibrated `config/cameras.json` on each computer; `source` is local to each computer, so pass the local camera index with `--source`.

On the server computer, from the project directory:
```powershell
python -m uvicorn fusion.server:app --host 0.0.0.0 --port 8000
```
Find its LAN IP with `python tools/lan_ip.py`. Allow Python/Uvicorn through the OS firewall on the private network if prompted.

On each camera computer, run one worker per attached camera, replacing `<SERVER_IP>` and the local camera index:
```powershell
python -m tools.camera_worker camA --source 1 --server ws://<SERVER_IP>:8000/ingest/camA
python -m tools.camera_worker camB --source 0 --server ws://<SERVER_IP>:8000/ingest/camB
```
Each worker runs detection locally and sends floor-projected detections plus a small annotated preview to the server. Open `http://<SERVER_IP>:8000/` in a browser on **both** computers to see the shared map and camera feeds. Keep the camera fixed after calibration and make sure all devices are on a network that permits device-to-device traffic. The map combines nearby same-class floor positions; it is a simple live fusion display, not persistent identity tracking.

## HTTPS for iPhones (and the Quest)
Mobile browsers only allow `getUserMedia` (camera) over HTTPS, except on localhost.

**Re-run `tools/make_certs.sh` on the venue Wi-Fi.** The certs include the LAN IP, which changes from network to network.

- Recommended: `brew install mkcert nss`, then `tools/make_certs.sh` → `certs/cert.pem`, `certs/key.pem`.
- Fallback (already generated): `certs/selfsigned.pem`, `certs/selfsigned-key.pem`.

Run the server with TLS:
```bash
uvicorn fusion.server:app --host 0.0.0.0 --port 8000 \
  --ssl-keyfile certs/key.pem --ssl-certfile certs/cert.pem
#  (or certs/selfsigned-key.pem / certs/selfsigned.pem)
```
For the Quest, the server also serves plain HTTP on `127.0.0.1:8001`, which `tools/quest_usb.sh` exposes via `adb reverse`. See CLAUDE.md, "Quest dev & debugging".
Phones open `https://<LAN_IP>:8000/` (from `tools/lan_ip.py`). Phone and laptop must be on the same network. Venue Wi-Fi often blocks device-to-device traffic, so check it with `tools/net_check.py`. If it fails, use an iPhone Personal Hotspot, or `tools/tunnel.sh` as a fallback. See the Networking section in CLAUDE.md.

Accepting the cert:
- **Self-signed, iPhone/Safari:** you can tap through the warning (Show Details → visit this website), but wss:// WebSockets may still fail. Don't rely on it.
- **Tunnel (`tools/tunnel.sh`):** a real cert, so nothing to install on the phone.
- **mkcert (no warnings):** run `tools/iphone_cert_help.sh`. It covers AirDropping `$(mkcert -CAROOT)/rootCA.pem` to the iPhone.
  - **iOS:** install the profile (Settings → Profile Downloaded → Install), then enable it under Settings → General → About → Certificate Trust Settings.
- Websockets have to use `wss://` on the same host:port as the page.

## Printing
Files: `printables/markers.pdf` (ArUco DICT_4X4_50, IDs 0-9, 180 mm; we use only **1, 3, 5, 7**) and `printables/chessboard.pdf` (9x6 inner corners, 25 mm). Regenerate with `python tools/make_printables.py`.
- Print on A4 at **100% / "Actual size"**. Turn **off** "Fit to page" / "Shrink to fit".
- Measure the **100 mm scale bar** on every page. If it's not 100 mm ± 1, reprint.
- Mount on stiff card or foam board and keep the white margin. Matte paper reduces glare.
- Corner markers 1, 3, 5, 7 lie flat on the floor at the corners of the space. The space is 1.5 m × 1.5 m. ID 1 is the origin, 7 is along +x, 5 is along +y and 3 is the opposite corner. Measure their centres into `config/markers.json`. `calib/extrinsics.py` measures how each sheet is rotated, so they don't need to be laid any particular way round.

## Layout
```
calib/       (A) intrinsics.py, extrinsics.py, viewer_pose.py      - stubs
perception/  (B) detect.py, segment.py, pose3d.py, floor_project.py - stubs
fusion/      (C) server.py, associate.py, tracker.py, walls.py, overlay.py, mock_feed.py - stubs
web/map/, web/viewer/  (D) empty index.html
config/      cameras.example.json, walls.example.json
tools/       smoke tests, printables + cert generators (prep tools)
printables/  PDFs     weights/  recordings/  certs/  (git-ignored except printables)
```

## Packing checklist
- [ ] Laptop + charger
- [ ] Extension lead
- [ ] USB hub
- [ ] Long USB cables
- [ ] 3-4 USB webcams (+ iPhones for Continuity Camera) + clamps/tripods
- [ ] Meta Quest 2 + charger; an iPhone as the fallback viewer
- [ ] iPhone for the hotspot + its charging cable
- [ ] USB-C DATA cable for Quest ↔ Mac-1 (adb)
- [ ] Gaffer tape
- [ ] Tape measure
- [ ] Printed markers + chessboard (scale verified)
- [ ] Cardboard/boards for walls
- [ ] Snacks
- [ ] **Message the organisers early for a corner of the room**
