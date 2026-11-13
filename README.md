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
```
On macOS, grant camera access to your terminal app first: **System Settings → Privacy & Security → Camera**. Then restart the terminal.

## HTTPS for phones
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
Phones open `https://<LAN_IP>:8000/` (from `tools/lan_ip.py`). Phone and laptop must be on the same network. Venue Wi-Fi often isolates clients, so use the travel router.

Accepting the cert:
- **Self-signed, Android/Chrome:** "Your connection is not private" → Advanced → Proceed.
- **Self-signed, iPhone/Safari:** "This connection is not private" → Show Details → visit this website → Visit Website.
- **mkcert (no warnings):** AirDrop or send `$(mkcert -CAROOT)/rootCA.pem` to the phone.
  - **iOS:** install the profile (Settings → Profile Downloaded → Install), then enable it under Settings → General → About → Certificate Trust Settings.
  - **Android:** Settings → Security → Encryption & credentials → Install a certificate → CA certificate.
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
- [ ] 3-4 USB webcams or spare phones + clamps/tripods
- [ ] Viewer phone
- [ ] Travel router
- [ ] Gaffer tape
- [ ] Tape measure
- [ ] Printed markers + chessboard (scale verified)
- [ ] Cardboard/boards for walls
- [ ] Snacks
- [ ] **Message the organisers early for a corner of the room**
