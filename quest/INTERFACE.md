# Quest viewer interface spec (for Role 2 and Role 3 agents)

Owner: Role 1 (Quest). Status: proposal. Items marked NEW are not yet in docs/CONTRACTS.md; Role 3 merges them there once the team agrees. Do not edit quest/ — this file only says what the Quest viewer needs from your parts.

## Current status — what is still missing (checked 2026-11-14 ~20:00 against fusion/server.py at 7f0327e)

The Quest viewer (quest/src/net.js) implements sections 1–3 exactly and **drops every message that doesn't match**,
so the real server still can't feed the headset directly. **Live path that works now:** the fusion server runs on
this Windows PC (`ws://10.247.206.87:8010/tracks`, see README "Live server"); Role 1's bridge turns it into this spec:
`python3 quest/tools/bridge.py` + `python3 tools/fusion_to_quest.py ws://10.247.206.87:8010/tracks --margin 0.2`.
Offline (Role 3's recording):

```bash
python3 quest/tools/bridge.py                                         # :8001, /world + /tracks per this spec
python3 tools/test_walk.py replay --url ws://127.0.0.1:8001/ingest    # samples/test_walk_camA.jsonl, loops
```

### Role 2 (fusion/server.py) — to do, in priority order
| # | Spec | Server today | Needed |
|---|---|---|---|
| 1 | §1 port 8001 | uvicorn on :8000 only | Also serve plain HTTP on 127.0.0.1:8001 (the Quest only reaches Mac-1 via `adb reverse tcp:8001`) — e.g. a second uvicorn on the same app. |
| 2 | §1 static | serves only the map page | `/quest/` (StaticFiles, `html=True`) and `/config/` from the repo, `.js` as `text/javascript`, `Cache-Control: no-store`. |
| 3 | §2 GET /world | `/api/world`, no `v`, `world_version`, `align_points`, `objects`, `zones` | Add `GET /world` with all §2 fields (`bounds` may stay as an extra; keep `/api/world` for the map if you like). |
| 4 | §3 first messages | none | On connect send `{"type":"world", ...}` then a tracks snapshot immediately. |
| 5 | §3 envelope | `{"t", "tracks"}` | Every message: `"type":"tracks"`, `"v":1`, `"world_version"`, `"zones":[]`. ~15 Hz (today 10 Hz), also when empty. |
| 6 | §3 `id` | **done** (7f0327e, fusion/tracker.py): stable per person, never reused | — |
| 7 | §3 `state`/Kalman | **done**: constant-velocity Kalman, `predicted` after 0.5 s unseen, sigma grows 0.35 m/s, dropped after 8 s or 2σ > 3 m (~4 s) | — |
| 8 | §3 track fields | **done**: `vel`, `cov`, `last_seen` (server receive time) added; `class_id/class_name` kept for the map | — |
| 9 | people only | people are tracker tracks; other classes still appear (ids ≥ 100000, `class_name` ≠ person, no vel/cov) | Drop non-person tracks for the Quest (`fusion_to_quest` already forwards people only), or run workers with `--classes 0`. |
| 10 | §5 test input | `/ingest/{camera_id}` takes a new payload (detections list + JPEG) | Also accept contract `detection` messages on `/ingest` (what `tools/test_walk.py replay` and the PIR send), or agree a change with Role 3. |

### Role 3 (config/, calib/) — to do
| # | Needed |
|---|---|
| 1 | `config/walls.json` with the real walls (does not exist yet → no walls in /world → "behind wall" can never trigger). ~5 cm accuracy. |
| 2 | **done**: markers.json lists 1, 7, 3, 5 in touch order (the bridge serves them as `align_points`). Wearers must touch the 4 corner markers in the order the headset prompts: **1 → 7 → 3 → 5** (round the square); touching in another order rotates everything. |
| 3 | `objects` (exits/doors/hazards) and PIR `zones` polygons for /world; PIR state into /tracks `zones`. |
| 4 | Merge the NEW items of this file into docs/CONTRACTS.md once Role 2 agrees. |

## 0. What the Quest does and does not need

- The Quest 2 is a thin client. It needs JSON only: no video, no images, no pixel bboxes.
- It aligns itself by the wearer touching floor markers. The server does NOT need to know where the wearer is, and the Quest does not send its pose.
- Everything is in the shared world frame: metres, x,y on the floor, z up, ArUco ID 1 = origin (corner markers 1, 7, 3, 5; see CLAUDE.md). Never send pixel or camera-frame coordinates to clients.

## 1. Serving (Role 2, fusion server) — needed by 16:00

The headset reaches Mac-1 only through `adb reverse tcp:8001 tcp:8001`, so everything the Quest uses must be on port 8001.

| Path | What | Notes |
|---|---|---|
| `/quest/` | Static files from the repo's quest/ folder, index.html as default | HTTP on 127.0.0.1:8001 (and the same on HTTPS :8000) |
| `/config/server_url.js` | The shared browser config module | Static |
| `GET /world` | World JSON (section 2) | |
| `WS /tracks` | Track stream (section 3) | Same port, same origin |
| `WS /events` | NEW Ping stream (section 4) | Same port, same origin |
| `POST /log` | NEW Quest remote debug log (section 1a) | Same port, same origin; must also work through the tunnel |

Requirements:

- `.js` files served as `text/javascript` (ES modules fail otherwise). FastAPI StaticFiles is fine.
- `Cache-Control: no-store` on `/quest/*` so a headset refresh always gets new code.
- No auth, no redirects, no CORS needed (same origin).

### 1a. POST /log — NEW (Role 2, small; Role 1's bridge already has it)

With `?debug=1` the Quest POSTs once a second (it is untethered over the tunnel, so there is no other console):

```json
{"client":"quest", "entries":[
  {"t":1763130000.1, "level":"error",  "msg":"..."},
  {"t":1763130000.2, "level":"warn",   "msg":"..."},
  {"t":1763130001.0, "level":"status", "msg":"fps 72 | ws open | align aligned | tracks 1 | age 0.05s | xr true"}]}
```

Server: append each entry as one JSON line (add `"client"`) to `logs/quest.log` (create `logs/`; add it to
.gitignore), reply `200 {"ok":true}`. No auth. Read it with `tail -f logs/quest.log`. If /log is missing the
Quest just shows its last 5 errors on its own debug panel, so this is nice-to-have.

## 2. GET /world (Role 3 owns the data, Role 2 serves it)

Also sent as the first message on /tracks connect, wrapped as `{"type":"world", ...}`.

```json
{
  "v": 1,
  "world_version": 3,
  "walls":   [{"a":[0.0,2.0], "b":[3.5,2.0], "h":2.0}],
  "cameras": [{"id":"camA", "pos":[0.2,0.1,2.1], "yaw":45, "fov":70}],
  "zones":   [{"sensor":"pir1", "zone":[[4,1],[5,1],[5,2],[4,2]]}],
  "objects": [{"id":"exit1", "type":"exit", "pos":[6.0,0.5], "size":[0.9,0.1,2.0], "label":"Fire exit"}],
  "align_points": [
    {"id":"m1", "marker":1, "pos":[0.0,0.0]},
    {"id":"m7", "marker":7, "pos":[1.5,0.0]},
    {"id":"m3", "marker":3, "pos":[1.5,1.5]},
    {"id":"m5", "marker":5, "pos":[0.0,1.5]}
  ]
}
```

- `walls`: every real wall/partition the demo cares about, measured in the world frame. The Quest uses these for its own hidden-vs-visible test, so they must match the real walls to ~5 cm.
- `objects` NEW: `type` is one of `exit | door | hazard | furniture | other`; `size` = [w, d, h] m.
- `align_points` NEW (Role 3, from calibration): map positions of the CENTRES of the FOUR floor markers (the corners of the tracked area) that the wearer touches to align the headset. The numbers above are placeholders. Rules:
  - exactly the 4 markers used for camera calibration, listed in touch order going round the area (today: 1, 7, 3, 5), `marker` = the printed ArUco ID, with their real measured positions in the world frame;
  - flat on the floor, taped down, never moved after calibration;
  - spread as far apart as the room allows, all reachable on foot, not under furniture;
  - the origin marker (ID 1) at [0,0].
  - The Quest draws these 4 corners permanently, so they must match the physical markers to ~3 cm.
- `world_version`: increment whenever any of the above changes; the Quest re-fetches on change.

## 3. WS /tracks (Role 2) — needed by 19:00

On connect: send the world message (section 2), then the current tracks snapshot immediately. Then send a tracks message at ~15 Hz, even when there are no tracks (it doubles as a heartbeat).

```json
{
  "type": "tracks",
  "v": 1,
  "t": 1763130000.123,
  "world_version": 3,
  "tracks": [
    {"id":7, "state":"visible", "pos":[2.10,3.40], "vel":[0.40,-0.10],
     "cov":[[0.02,0.00],[0.00,0.02]], "last_seen":1763130000.050, "seen_by":["camA","camB"]}
  ],
  "zones": [{"sensor":"pir1", "active":true, "t":1763129999.900}]
}
```

Field rules:

- `t`, `last_seen`: server Unix time in seconds (float). `last_seen` = capture time of the last frame that saw the person, not the processing time.
- `id`: integer, stable for as long as the person is tracked, never reused in a server run.
- `state`: `visible` (a camera saw them this cycle) or `predicted` (no camera; Kalman only). Hidden-behind-a-wall is decided by the Quest, not the server.
- `pos`: the person's foot point on the floor, metres. `vel`: m/s. `cov`: 2x2 position covariance in m², symmetric, positive definite, growing while predicted.
- Drop a track from the list once it has been predicted for more than ~8 s or its 2-sigma radius exceeds ~3 m. No separate "lost" message.
- `zones` NEW: current PIR state, so clients don't need another socket.
- No NaN/Infinity, no nulls inside arrays. **Exception:** `skeleton.kp` uses `null` for an unknown joint (§3a).
- `skeleton` NEW, optional: see §3a.

Quality targets the overlay depends on:

- Foot-point error under ~15 cm in the overlap areas (box should wrap the real person).
- Capture-to-send latency under ~150 ms. The Quest extrapolates with vel, so a correct vel matters more than a perfectly smooth pos.

## 3b. Camera states — NEW, optional (fusion server; not yet passed through the bridge)

The fusion server's `/tracks` carries `"cameras": [{"id","pos","yaw","fov","state","weight","fps","lag_ms"}, ...]` once
per second (also `GET /cameras`). `state` is TRUSTED / DEGRADED / EXCLUDED / OFFLINE (see docs/CONTRACTS.md "Camera
states"). Viewers may colour camera frusta by state (green / amber / orange / grey). Ignoring the field is fine.

## 3a. Pose skeleton on tracks — NEW, optional (Role 2 server, Role 3 worker; Role 1 renders)

Each track may carry a skeleton; omitted when there is none. Clients that don't know the field ignore it.

```json
{"id":7, "state":"visible", "pos":[2.10,3.40], ...,
 "skeleton": {"kp": [[2.08,3.41,1.66,0.93], null, ...17 entries], "src":"camB", "age":0.12, "frozen":false}}
```

- `kp`: exactly 17 joints in COCO-17 order, each `[x, y, z, conf]` in world metres (z up, floor = 0), or `null` if
  unknown. Joints come from one camera, lifted onto a vertical plane through the foot point that faces that camera:
  height and sideways position are good, depth along that camera's view is approximate (~±0.2 m).
- `src`: camera the joints came from. `age`: seconds since that camera last saw them.
- `frozen`: `true` while the track is `predicted`. The last skeleton is moved with the track (Kalman displacement since
  `last_seen`) but the pose is not updated. Dropped after 3 s frozen. Suggest drawing frozen skeletons dimmer/dashed.
- Path: worker `detection.kp3d` → fusion server (best view, EMA-smoothed, null joints held 0.5 s) → `/tracks` →
  `tools/fusion_to_quest.py` → `quest/tools/bridge.py` (passes it through untouched) → Quest.

COCO-17 joint order (index: name): 0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear, 5 left_shoulder,
6 right_shoulder, 7 left_elbow, 8 right_elbow, 9 left_wrist, 10 right_wrist, 11 left_hip, 12 right_hip, 13 left_knee,
14 right_knee, 15 left_ankle, 16 right_ankle. ("left" = the person's left.)

Bones to draw (pairs of joint indices):
`[[5,7],[7,9],[6,8],[8,10],[5,6],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16],[0,5],[0,6]]`
Skip a bone if either end is `null`.

## 4. WS /events — pings NEW (Role 2) — needed by 23:00

Two-way. Any client (Quest or command map) may send; the server stores active pings and relays every message to all clients, including the sender.

Client → server:

```json
{"type":"ping", "id":"quest1-12", "from":"quest1", "kind":"person", "target":7,
 "pos":[2.1,3.4], "label":"check here", "ttl":60}
{"type":"ping_clear", "id":"quest1-12"}
```

Server → clients: the same messages with `"t"` (server receive time) added, plus on connect:

```json
{"type":"pings", "t":1763130000.0, "pings":[ ...active pings... ]}
```

Rules:

- `id` is chosen by the sender (`<client>-<counter>`), unique per client.
- `kind`: `person` (target = track id), `object` (target = object id) or `location` (target = null).
- The server removes a ping after `ttl` seconds and broadcasts `ping_clear`.
- A person ping keeps following its track; if the track is dropped, the server keeps the ping at the track's last pos.
- Command map (Role 2, web/map/): click on the map → location ping, click on a track → person ping, show all active pings, button to clear.

## 5. Testing without cameras (Role 2, strongly requested)

A small publisher, e.g. `fusion/mock_publisher.py`, that feeds the real server one fake person walking a loop (including through a blind spot) using the world from section 2, so the Quest, the command map and the server can be tested before the cameras work.

Quick checks any agent can run:

```bash
curl -s http://127.0.0.1:8001/world | python3 -m json.tool
curl -sI http://127.0.0.1:8001/quest/index.html          # 200, no-store
python3 -c "import websocket,json;w=websocket.create_connection('ws://127.0.0.1:8001/tracks');print(w.recv()[:300]);print(w.recv()[:300])"
```

## 6. Summary of who provides what

| Owner | Provides | By |
|---|---|---|
| Role 2 | Static serving + /world + /config/server_url.js on :8001, no-store | 16:00 |
| Role 3 | walls, cameras, align_points in the world frame, markers taped down | 16:00 |
| Role 2 | /tracks per section 3, heartbeat when empty | 19:00 |
| Role 2 | Mock publisher | 19:00 |
| Role 3 | objects, PIR zones wired into /tracks | 23:00 |
| Role 2 | /events + command-map pinging | 23:00 |
