# Contracts

Shared file: change it only after telling the team. If this file and CLAUDE.md disagree, CLAUDE.md wins.

## World frame
- Corner markers ArUco **1, 3, 5, 7** (DICT_4X4_50, 180 mm) lie flat on the floor at the corners of the demo space (config/markers.json). The space is 1.5 m × 1.5 m: ID 1 = origin (0,0), ID 7 along +x (1.5,0), ID 5 along +y (0,1.5), ID 3 the opposite corner (1.5,1.5). No other marker IDs are used.
- Units: **metres**. `x`, `y` lie on the floor plane and `z` points up (right-handed).
- Assumed person height: **1.75 m**.
- Timestamps: unix seconds (float).
- Camera names: `camA` (Mac-1), `camB` (UNO Q, behind the wall), `camC` (Mac-2).

## Endpoints (fusion server on Mac-1, HTTPS)
| Endpoint | Direction | Payload |
|---|---|---|
| `wss://<server>/ingest` | camera workers + PIR → server | `detection`, `zone` |
| `wss://<server>/tracks` | server → all clients, ~15 Hz | `tracks` (and `world` on connect) |
| `GET https://<server>/world` | clients | `world` |

Mac-1 also serves the same endpoints over plain HTTP on `127.0.0.1:8001`, which the Quest reaches via `adb reverse`.
The server URL comes from `config/server.json` or the `SERVER_URL` env var. Never hard-code IPs.

## Messages (JSON)

### detection (camera worker → server, /ingest)
```json
{"cam": "camA", "t": 1731500000.123, "foot": [x, y], "bbox": [x, y, w, h], "conf": 0.9}
```
- `foot` is in world metres. `bbox` is in image pixels.
- Optional stretch field: `"mask_png": "<b64>"`.
- Optional **`kp3d`** (pose skeleton, worker run with `--pose`): exactly 17 entries in COCO-17 order (below), each
  `[x, y, z, conf]` in world metres (z up, floor = 0) or `null` for a joint not seen (conf < 0.3). Omitted or `null`
  when there is no usable skeleton (small person, < 8 joints). Joints are lifted onto a vertical plane through the
  foot point, facing the camera, so depth along the camera ray is approximate (~±0.2 m); height and sideways are good.

```json
{"cam": "camA", "t": 1731500000.123, "foot": [x, y], "bbox": [x, y, w, h], "conf": 0.9,
 "kp3d": [[0.71, 0.52, 1.66, 0.93], null, ... 17 entries]}
```

### zone (PIR → server, /ingest)
```json
{"sensor": "pir1", "t": 1731500000.1, "zone": [[x, y], ...], "active": true}
```
The server uses an active zone as a loose position measurement: its centroid, with a large covariance.

### tracks (server → all clients, /tracks, ~15 Hz)
```json
{"t": 1731500000.2,
 "tracks": [{"id": 7, "state": "visible|predicted", "pos": [x, y], "vel": [vx, vy],
             "cov": [[a, b], [b, c]], "last_seen": 1731500000.1, "seen_by": ["camB"]}]}
```
- `visible`: some camera saw the track recently. `predicted`: no camera has seen it for more than 0.5 s, so the position is a Kalman prediction (sigma grows 0.35 m/s). Dropped after 8 s predicted or once 2σ > 3 m (~4 s). Implemented in fusion/tracker.py; `id` is stable per person. The server also sends `class_id`/`class_name` (map) and groups other classes per frame with ids ≥ 100000 (no vel/cov); viewers should use `class_name == "person"` only.
- **The server does not send "hidden".** Each viewer decides hidden vs visible itself, by testing line of sight from its own position to the track against the walls.
- Optional **`skeleton`** per track (omitted when the track has none):
  `{"kp": [[x, y, z, conf] | null, ... 17], "src": "camB", "age": 0.12, "frozen": false}`
  - `kp`: 17 joints, COCO-17 order, world metres (z up), `null` = joint unknown. Smoothed by the server.
  - `src`: camera the joints came from (the one with the best view: highest mean joint conf, tie → larger bbox).
  - `age`: seconds since that camera last saw the joints.
  - `frozen`: `true` while the track is `predicted`. The last skeleton is translated with the Kalman displacement
    since `last_seen` (pose not updated). Dropped after 3 s frozen.

### Camera states (optional fields, backward compatible)
| State | Weight | Meaning | Map colour |
|---|---|---|---|
| `TRUSTED` | 1.0 | markers where expected (or a good re-solve) | green |
| `DEGRADED` | `degraded_weight` (0.4) | poor re-solve, or reconnected and not yet verified | amber |
| `EXCLUDED` | 0 | markers say the camera moved and it can't re-solve; sends preview heartbeats, no detections | orange |
| `OFFLINE` | 0 | socket closed, final OFFLINE status, or no message for `stale_timeout` (2.5 s), decided by the server | grey |
- Worker → server messages (detections and `{"type":"status",...}`) carry `"state"`, `"weight"`, `"reasons"`.
  Old workers without them are treated as TRUSTED (server logs a warning).
- EXCLUDED/OFFLINE cameras contribute nothing: their detections are purged, they leave every track's `seen_by`
  and skeleton selection; tracks only they saw go `predicted` (same as a person going missing).
- Exposed as `{"id","pos","yaw","fov","state","weight","reasons","fps","lag_ms",...}` in `GET /cameras`,
  `GET /api/world` `cameras`, and a `"cameras"` list in `/tracks` messages once per second.
- Settings: config/refresh.json (calib/refresh.py has the rules).

### Pose skeleton (COCO-17)
COCO-17 joint order (index: name): 0 nose, 1 left_eye, 2 right_eye, 3 left_ear, 4 right_ear, 5 left_shoulder,
6 right_shoulder, 7 left_elbow, 8 right_elbow, 9 left_wrist, 10 right_wrist, 11 left_hip, 12 right_hip, 13 left_knee,
14 right_knee, 15 left_ankle, 16 right_ankle. ("left" = the person's left.)

Bones to draw (pairs of joint indices):
`[[5,7],[7,9],[6,8],[8,10],[5,6],[5,11],[6,12],[11,12],[11,13],[13,15],[12,14],[14,16],[0,5],[0,6]]`
Skip a bone if either end is `null`.
All skeleton fields are optional and backward compatible: clients that don't know them ignore them.

### world (server → clients on connect, and GET /world)
```json
{"walls": [{"a": [x, y], "b": [x, y], "h": 2.0}],
 "cameras": [{"id": "camA", "pos": [x, y, z], "yaw": 0.0, "fov": 70.0}],
 "zones": [...]}
```
Walls are 2D floor segments from `a` to `b` with height `h` in metres. `yaw` and `fov` are in degrees.

## Viewer rendering (Quest, computed client-side)
- **visible** (line of sight is clear): thin outline capsule.
- **hidden** (a camera sees the person but a wall blocks line of sight): glowing cyan capsule, label "via <seen_by>", with the blocking wall highlighted.
- **predicted**: translucent orange capsule plus a floor fan sized by `cov`, label "last seen Ns".
- Quest passthrough is greyscale, so use bright cyan and orange.
