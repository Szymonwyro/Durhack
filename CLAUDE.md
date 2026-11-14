# DurHack 2026 — "see through walls" situational awareness

## What we're building (one paragraph)
Fixed cameras around a room are calibrated to one shared floor map using ArUco markers.
Each camera detects people (YOLO), converts their foot point to floor coordinates
(flat-floor assumption), and sends detections to a fusion server. The server matches
detections across cameras, tracks each person with a constant-velocity Kalman filter,
and labels tracks visible / hidden-behind-wall / predicted (no camera sees them;
uncertainty grows). A Meta Quest 2 viewer, aligned to the same map, shows people
through walls as 3D ghosts, with predicted paths when nobody can see them. A top-down
command map shows everything. An Arduino UNO Q is the camera behind the wall, plus a
PIR sensor in the blind spot and an LED-matrix radar. Pitch framing: shared situational
awareness for firefighters and search & rescue. Never use "Lattice"/"Anduril" in the product.

## Hardware we have
- 2x MacBook (Mac-1 = server + camera A + Quest dev/debug over adb; Mac-2 = camera C / dev)
- 1x Windows PC (optional: big-screen command map display and/or an extra camera worker with a
  USB camera; not part of the Quest workflow)
- 1x Meta Quest 2 (viewer; passthrough is GREYSCALE, so use bright cyan/orange visuals)
- 1x Arduino UNO Q (Debian Linux on Qualcomm QRB2210 + STM32U585 MCU, Wi-Fi,
  8x13 LED matrix, 3.3V headers, set up via Arduino App Lab; Python on the Linux side,
  sketch on the MCU, joined by the Bridge)
- USB cameras + accessories, breadboard, jumper wires, basic electronics
  (PIR, buzzer, LEDs, resistors as available)
- iPhones (all team phones): Personal Hotspot, fallback 2D AR viewer (no WebXR on iOS),
  Quest casting via the Meta Horizon app, optional Continuity Camera webcam
- Printed ArUco corner markers IDs 1, 3, 5, 7 (DICT_4X4_50, 180 mm, flat on the floor; ID 1 = origin), chessboard (9x6, 25 mm)
- No travel router (see Networking)

## Networking (no router)
- **First check:** venue Wi-Fi may block device-to-device traffic (client isolation). Run
  `tools/net_check.py <server_url>` from another device before relying on it
  (on an iPhone: open `<server>/world` in Safari).
- **Primary if venue Wi-Fi fails:** an iPhone Personal Hotspot; all devices join it.
  ~5 device limit: Mac-1, Mac-2, Quest, UNO Q (Windows only if room). Keep that iPhone charging.
  If a device can't see or join it, turn on "Maximize Compatibility" (forces 2.4 GHz).
  After joining (and every time the hotspot changes): `tools/make_certs.sh` on Mac-1, then set
  `lan_url` in config/server.json.
- **Fallback:** cloudflared quick tunnel on Mac-1 (`tools/tunnel.sh`) → public https URL with a
  valid cert, works from any network, adds ~50-150 ms (fine for detections/tracks, not video).
  It writes `tunnel_url` into config/server.json; the URL changes on every run.
- **Quest:** USB/wireless adb from Mac-1 → http://localhost:8001 (see "Quest dev & debugging").
- **iPhones + certs:** on iOS, tapping through a self-signed warning can still break wss://
  WebSockets. Use (a) the tunnel URL (real cert), or (b) mkcert's root CA installed and fully
  trusted on each iPhone: `tools/iphone_cert_help.sh` prints the file and the steps.
- **Server URL:** all clients read it from config/server.json
  (`{"mode":"lan|tunnel|usb","lan_url":"https://<ip>:8000","tunnel_url":"","usb_url":"http://localhost:8001"}`)
  or the SERVER_URL env var, via `config/server_url.py` (Python) / `config/server_url.js`
  (browser; `?server=` / `?mode=` override). Never hard-code IPs: they change every time we switch network.

## Quest dev & debugging (Mac-1, not Windows)
- adb: `brew install android-platform-tools` on Mac-1. Meta Quest Developer Hub for macOS is optional.
- Setup: Developer Mode in the Meta Horizon iPhone app → Quest to Mac-1 with a USB-C DATA cable →
  accept "Allow USB debugging" (always allow) in the headset → `adb devices` lists it.
- Serving: the server runs HTTPS on :8000 and plain HTTP on :8001 bound to 127.0.0.1.
  `tools/quest_usb.sh` runs `adb reverse tcp:8001 tcp:8001` → open http://localhost:8001/quest/
  in the Quest browser (a secure context for WebXR, so no cert is needed).
- Wireless: `tools/quest_usb.sh --wireless` (after USB: `adb tcpip 5555` + `adb connect <quest-ip>:5555`;
  same network only).
- Debugging: Chrome on Mac-1 → chrome://inspect → the Quest browser's tabs + console.
- Audience view: cast from the Quest to oculus.com/casting in a Mac browser, or to the Meta Horizon iPhone app.
- Quest Link / Air Link are Windows-only and NOT needed: WebXR runs in the headset browser.

## iPhones (all team phones)
- Hotspot: see Networking.
- **iOS Safari has NO WebXR.** The iPhone fallback viewer is a 2D AR overlay (getUserMedia camera +
  canvas overlay from the server) in web/viewer/, owned by Role 1 as the Quest fallback.
- Certs: see Networking (tunnel URL or mkcert root CA; self-signed can break wss://).
- Extra camera: Continuity Camera turns an iPhone into a webcam for a Mac (mount it landscape,
  locked, near the Mac). While attached it SHIFTS OpenCV camera indices, so cameras MUST be chosen
  by name/resolution via config/cameras.json. Option for camera C: iPhone via Continuity Camera on Mac-2.

## Handoffs
- **Laptop 2 = fusion server + camB:** docs/HANDOFF_LAPTOP2_SERVER_CAMB.md. Mac-1 runs the camA worker into it.
  Camera indices are per laptop: always pass `--source N` (camB has no `source` in cameras.json on purpose).
- **Current server: Mac-1, `http://10.247.46.145:8010/`** (port 8010; laptop 2 is down). camA streams into it.
  The server tracks people (fusion/tracker.py: stable ids, Kalman, visible/predicted; tests in tests/).
  Quest alignment: touch corners 1 → 7 → 3 → 5. Skeleton pipeline (`--pose`, `kp3d`, track `skeleton`) is built
  through the server but not in use yet; fusion_to_quest/bridge pass-through not done.
  Quest: `python3 tools/fusion_to_quest.py ws://10.247.46.145:8010/tracks` next to the bridge (README "Live server").
- **Live cameras → Quest (Role 1):** until fusion/server.py speaks quest/INTERFACE.md, run Role 1's bridge and feed it
  the fusion server's fused positions: `python3 quest/tools/bridge.py` + `python3 tools/fusion_to_quest.py
  ws://<laptop2-ip>:8000/tracks` (laptop 2's IP is in README.md). Bridge = stable ids, Kalman, world/align_points on :8001.
- **Test data without cameras (Role 1/2):** samples/test_walk_camA.jsonl = 60 s of one person (Szymon) walking in
  and out of the 1.5 m square, seen by camA, as contract detection messages in world metres. Replay it into any
  server's WS /ingest at real pace, looping: `python3 tools/test_walk.py replay [--url ws://127.0.0.1:8001/ingest]`
  (e.g. into `quest/tools/bridge.py`, which turns it into /tracks). `--print` just prints it.

## Device roles
| Device | Runs |
|---|---|
| Mac-1 | fusion server (FastAPI, HTTPS :8000 + HTTP :8001 on 127.0.0.1 for the Quest), camera A worker, Quest dev/debug (adb, chrome://inspect) |
| UNO Q + USB cam | camera B (behind wall) worker, PIR blind-spot sensor, LED radar |
| Mac-2 | camera C worker: USB cam or an iPhone via Continuity Camera (fixed, taped down after calibration) |
| Windows (optional) | big-screen command map and/or an extra camera worker with a USB camera |
| Quest 2 | WebXR immersive-ar viewer in the headset browser |
| iPhones | Personal Hotspot, fallback 2D AR viewer (web/viewer/), casting display |

## World frame (fixed — do not change without telling the team)
Corner markers ArUco 1, 3, 5, 7 flat on the floor of the 1.5 m x 1.5 m space (config/markers.json):
ID 1 = origin (0,0), ID 7 along +x (1.5,0), ID 5 along +y (0,1.5), ID 3 opposite corner (1.5,1.5).
Sheet rotations are measured automatically by calib/extrinsics.py. Metres. x,y on the floor, z up. Person height 1.75 m. Only IDs 1, 3, 5, 7 are used.
Calibration, on the laptop the camera is plugged into: `python -m calib.intrinsics <cam> --source <N>` (chessboard, lens)
then `python -m calib.position <cam>` (corner markers → camera position; picks the camera, offers commit+push).
Neither changes `source` (per-laptop index) or markers.json yaws (use calib/extrinsics.py with all 4 markers for those).
While running, tools/camera_worker.py re-solves its camera's position from the 4 corner markers at startup and every 60 s
(calib/auto_pose.py, `--pose-interval`, 0 = off). The pose is kept in memory and sent to the server (not written to git).

## Message contracts (see docs/CONTRACTS.md; additions here)
- detection (camera worker → server, WebSocket /ingest):
  {"cam":"camA","t":<unix>,"foot":[x,y],"bbox":[x,y,w,h],"conf":0.9}
- zone (PIR → server): {"sensor":"pir1","t":<unix>,"zone":[[x,y],...],"active":true}
- tracks (server → all clients, WebSocket /tracks, ~15 Hz):
  {"t":<unix>,"tracks":[{"id":7,"state":"visible|predicted","pos":[x,y],"vel":[vx,vy],
   "cov":[[a,b],[b,c]],"last_seen":<unix>,"seen_by":["camB"]}]}
- world (server → clients on connect, and GET /world):
  {"walls":[{"a":[x,y],"b":[x,y],"h":2.0}],"cameras":[{"id":"camA","pos":[x,y,z],"yaw":deg,"fov":deg}],
   "zones":[...]}
- Hidden-vs-visible is computed BY THE VIEWER (line of sight from its own position
  through the walls), not by the server.

## Repo ownership — only edit your own folders unless asked
- Role 1 (Quest):        quest/, web/viewer/ (iPhone fallback viewer)
- Role 2 (Detection+Map): perception/, fusion/, web/map/
- Role 3 (Cameras+Lead):  calib/, edge/ (UNO Q), config/, tools/, docs/, README
Shared: CLAUDE.md, docs/CONTRACTS.md — change only after telling the team.

## Conventions
- Server URL comes from config/server.json (or the SERVER_URL env var); never hard-code IPs.
- Identify cameras by name in config/cameras.json, never by OpenCV index alone (indices shift,
  e.g. when an iPhone attaches via Continuity Camera).
- No video, weights or certs in git. Recordings go in recordings/ (gitignored).
- Small commits to main, often. Prefix messages with role: "[r2] kalman update step".
- Prefer simple, readable Python/JS. A working demo matters more than perfect code.
- Save tokens: don't read recordings/, weights/ or large logs; tail logs.

## Checkpoints (Sat → Sun; adjust to the real deadline)
- 16:00  one real camera → dot on map; Quest shows a mock track at the right spot after alignment
- 19:00  cams A+B fused, Kalman prediction in blind spot, UNO Q sending detections
- 23:00  Quest shows a ghost through the real wall from a real camera; PIR shrinks prediction; LED radar
- 07:00  CORE FROZEN, tag v1-demo
- 09:00+ rehearse 5x, backup video, Devpost

## Fallbacks
- Quest passthrough fails → Quest shows a VR replica of the room; an iPhone becomes the AR viewer
  via web/viewer/: 2D AR overlay (getUserMedia camera + canvas overlay from the server), NOT WebXR
  (iOS Safari doesn't support it)
- UNO Q too slow for detection (<2 fps) → it streams frames to Mac-1 for detection; keeps PIR + LEDs
- ID swaps between people → demo with one person in a bright top
