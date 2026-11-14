# Contracts

Shared file: change it only after telling the team. If this file and CLAUDE.md disagree, CLAUDE.md wins.

## World frame
- Origin: ArUco **ID 0** (DICT_4X4_50, 180 mm), lying flat on the floor.
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

The server URL comes from `config/server.json` or the `SERVER_URL` env var. Never hard-code IPs.

## Messages (JSON)

### detection (camera worker → server, /ingest)
```json
{"cam": "camA", "t": 1731500000.123, "foot": [x, y], "bbox": [x, y, w, h], "conf": 0.9}
```
- `foot` is in world metres. `bbox` is in image pixels.
- Optional stretch fields: `"mask_png": "<b64>"` and `"kp3d": [[x, y, z], ...]` (world frame).

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
- `visible`: some camera saw the track recently. `predicted`: no camera has seen it for more than 0.5 s, so the position is a Kalman prediction. Tracks are dropped after 10 s unseen.
- **The server does not send "hidden".** Each viewer decides hidden vs visible itself, by testing line of sight from its own position to the track against the walls.

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
