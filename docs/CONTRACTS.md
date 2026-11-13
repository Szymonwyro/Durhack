# Contracts

Agree on these in H0-1 and change them only with the whole team.

## World frame
- Origin: ArUco **ID 0** (DICT_4X4_50, 180 mm), lying flat on the floor.
- Units: **metres**. `x`, `y` lie on the floor plane and `z` points up (right-handed).
- Walls are 2D floor segments `[[x1,y1],[x2,y2]]` with a `height_m` (`config/walls.json`).
- Assumed person height: **1.75 m**, used when projecting silhouettes.
- Timestamps: unix seconds (float).

## Messages (JSON)

### detection (cam worker → server)
```json
{"cam": "cam2", "t": 1731500000.123, "foot": [x, y], "bbox": [x, y, w, h],
 "mask_png": "<b64, optional>", "kp3d": [[x, y, z], ...]}
```
`foot` is in world metres. `bbox` is in image pixels. `kp3d` is optional and in the world frame.

### track (server → map & viewer)
```json
{"id": 7, "state": "visible|hidden|predicted", "pos": [x, y], "vel": [vx, vy],
 "cov": [[..], [..]], "last_seen": 1731500000.1, "seen_by": ["cam2"]}
```

### overlay (server → viewer)
```json
{"viewer_pose": {"R": [...], "t": [...]},
 "items": [{"id": 7, "style": "solid|ghost|predicted", "box2d": [...], "kp2d": [...],
            "cutout": "<b64, optional>", "clip_poly": [[u, v], ...]}]}
```
- `solid`: visible to the viewer directly.
- `ghost`: occluded from the viewer but seen by another camera, clipped to the wall outline (`clip_poly`).
- `predicted`: seen by no camera. Draw a dashed outline and an uncertainty cone.
