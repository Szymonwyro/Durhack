# DurHack prep: multi-camera situational awareness

> **Prep only.** This repo holds the environment, model weights, printables, empty stubs, config
> templates, smoke tests and docs. All project logic (detection, tracking, fusion, calibration,
> server endpoints, frontends) is written **during the event**, as the hackathon rules require.

Docs: [CONTRACTS](docs/CONTRACTS.md) · [PLAN](docs/PLAN.md) · [DEMO_SCRIPT](docs/DEMO_SCRIPT.md)

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
Files: `printables/markers.pdf` (ArUco DICT_4X4_50, IDs 0-9, 180 mm) and `printables/chessboard.pdf` (9x6 inner corners, 25 mm). Regenerate with `python tools/make_printables.py`.
- Print on A4 at **100% / "Actual size"**. Turn **off** "Fit to page" / "Shrink to fit".
- Measure the **100 mm scale bar** on every page. If it's not 100 mm ± 1, reprint.
- Mount on stiff card or foam board and keep the white margin. Matte paper reduces glare.
- ID 0 is the world origin and lies flat on the floor. Use the other IDs on walls (viewer localisation) or as extra floor references.

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
