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
- 2x MacBook (Mac-1 = server + camera A; Mac-2 = camera C / dev)
- 1x Windows PC (Quest dev via Meta Quest Developer Hub / adb, audience map display)
- 1x Meta Quest 2 (viewer; passthrough is GREYSCALE, so use bright cyan/orange visuals)
- 1x Arduino UNO Q (Debian Linux on Qualcomm QRB2210 + STM32U585 MCU, Wi-Fi,
  8x13 LED matrix, 3.3V headers, set up via Arduino App Lab; Python on the Linux side,
  sketch on the MCU, joined by the Bridge)
- USB cameras + accessories, breadboard, jumper wires, basic electronics
  (PIR, buzzer, LEDs, resistors as available)
- Phones (fallback viewer, casting the Quest view)
- Printed ArUco markers (DICT_4X4_50, 180 mm, ID 0 = world origin flat on floor), chessboard (9x6, 25 mm)
- No travel router (see Networking)

## Networking (no router)
- **First check:** venue Wi-Fi may block device-to-device traffic (client isolation). Run
  `tools/net_check.py <server_url>` from another device before relying on it.
- **Primary:** one phone's personal hotspot (or Windows Mobile Hotspot); all devices join it.
  iPhone hotspot ≈5 devices max: Mac-1, Mac-2, Quest, UNO Q (+ Windows if room). Keep the phone charging.
  After joining (and every time the hotspot changes): `tools/make_certs.sh` on Mac-1, then set
  `lan_url` in config/server.json.
- **Fallback:** cloudflared quick tunnel on Mac-1 (`tools/tunnel.sh`) → public https URL with a
  valid cert, works from any network, adds ~50-150 ms (fine for detections/tracks, not video).
  It writes `tunnel_url` into config/server.json; the URL changes on every run.
- **Quest dev:** USB + `adb reverse tcp:8000 tcp:8000` (`tools/quest_usb.sh`) → http://localhost:8000
  (secure context, no cert needed). Caveat: a server running TLS on 8000 doesn't answer plain http,
  so use https://localhost:8000 (accept the cert once) or serve plain HTTP for the Quest.
- **Server URL:** all clients read it from config/server.json
  (`{"mode":"lan|tunnel|usb","lan_url":...,"tunnel_url":...,"usb_url":...}`) or the SERVER_URL env var,
  via `config/server_url.py` (Python) / `config/server_url.js` (browser; `?server=` / `?mode=` override).
  Never hard-code IPs: they change every time we switch network.

## Device roles
| Device | Runs |
|---|---|
| Mac-1 | fusion server (FastAPI, HTTPS), camera A worker |
| UNO Q + USB cam | camera B (behind wall) worker, PIR blind-spot sensor, LED radar |
| Mac-2 | camera C worker (fixed, taped down after calibration) |
| Windows | Quest dev + command map on a big screen |
| Quest 2 | WebXR immersive-ar viewer |

## World frame (fixed — do not change without telling the team)
ArUco ID 0 flat on the floor = origin. Metres. x,y on the floor, z up. Person height 1.75 m.

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
- Role 1 (Quest):        quest/
- Role 2 (Detection+Map): perception/, fusion/, web/map/
- Role 3 (Cameras+Lead):  calib/, edge/ (UNO Q), config/, tools/, docs/, README
Shared: CLAUDE.md, docs/CONTRACTS.md — change only after telling the team.

## Conventions
- Server URL comes from config/server.json (or the SERVER_URL env var); never hard-code IPs.
- Identify cameras by name in config/cameras.json, never by OpenCV index alone (indices shift).
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
- Quest passthrough fails → Quest shows a VR replica of the room; phone becomes the AR viewer
- UNO Q too slow for detection (<2 fps) → it streams frames to Mac-1 for detection; keeps PIR + LEDs
- ID swaps between people → demo with one person in a bright top
