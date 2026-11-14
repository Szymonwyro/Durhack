# Quest viewer interface spec (for Role 2 and Role 3 agents)

Owner: Role 1 (Quest). Status: proposal. Items marked NEW are not yet in docs/CONTRACTS.md; Role 3 merges them there once the team agrees. Do not edit quest/ — this file only says what the Quest viewer needs from your parts.

## 0. What the Quest does and does not need

- The Quest 2 is a thin client. It needs JSON only: no video, no images, no pixel bboxes.
- It aligns itself by the wearer touching floor markers. The server does NOT need to know where the wearer is, and the Quest does not send its pose.
- Everything is in the shared world frame: metres, x,y on the floor, z up, ArUco ID 0 = origin. Never send pixel or camera-frame coordinates to clients.

## 1. Serving (Role 2, fusion server) — needed by 16:00

The headset reaches Mac-1 only through `adb reverse tcp:8001 tcp:8001`, so everything the Quest uses must be on port 8001.

| Path | What | Notes |
|---|---|---|
| `/quest/` | Static files from the repo's quest/ folder, index.html as default | HTTP on 127.0.0.1:8001 (and the same on HTTPS :8000) |
| `/config/server_url.js` | The shared browser config module | Static |
| `GET /world` | World JSON (section 2) | |
| `WS /tracks` | Track stream (section 3) | Same port, same origin |
| `WS /events` | NEW Ping stream (section 4) | Same port, same origin |

Requirements:

- `.js` files served as `text/javascript` (ES modules fail otherwise). FastAPI StaticFiles is fine.
- `Cache-Control: no-store` on `/quest/*` so a headset refresh always gets new code.
- No auth, no redirects, no CORS needed (same origin).

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
    {"id":"m0", "marker":0, "pos":[0.0,0.0]},
    {"id":"m1", "marker":1, "pos":[4.0,0.0]},
    {"id":"m2", "marker":2, "pos":[4.0,3.0]},
    {"id":"m3", "marker":3, "pos":[0.0,3.0]}
  ]
}
```

- `walls`: every real wall/partition the demo cares about, measured in the world frame. The Quest uses these for its own hidden-vs-visible test, so they must match the real walls to ~5 cm.
- `objects` NEW: `type` is one of `exit | door | hazard | furniture | other`; `size` = [w, d, h] m.
- `align_points` NEW (Role 3, from calibration): map positions of the CENTRES of the FOUR floor markers (the corners of the tracked area) that the wearer touches to align the headset. The numbers above are placeholders. Rules:
  - exactly the 4 markers used for camera calibration, listed in touch order (0, 1, 2, 3, going round the area), with their real measured positions in the world frame;
  - flat on the floor, taped down, never moved after calibration;
  - spread as far apart as the room allows, all reachable on foot, not under furniture;
  - marker 0 at [0,0].
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
- No NaN/Infinity, no nulls inside arrays.

Quality targets the overlay depends on:

- Foot-point error under ~15 cm in the overlap areas (box should wrap the real person).
- Capture-to-send latency under ~150 ms. The Quest extrapolates with vel, so a correct vel matters more than a perfectly smooth pos.

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
