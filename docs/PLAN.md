# Plan

## Roles
- **A: Calibration & cameras.** Intrinsics (chessboard), extrinsics (solvePnP on ArUco), camera ingest workers, smoothed viewer phone pose, physical setup, blind spot, `walls.json`.
- **B: Perception.** Record clips in the first 30 min. YOLO → foot point → floor coords, then seg masks, then MediaPipe 3D pose → world frame.
- **C: Fusion server.** FastAPI + websockets, `mock_feed` in the first hour, Hungarian association, Kalman CV, track states, wall model + line-of-sight, wall-constrained prediction, overlay projection + wall clip polygon.
- **D: Frontends & pitch.** Command map, viewer AR app (3 styles, clipped to wall), ElevenLabs callouts, product name, Devpost, demo script, backup video, pitch.

## Timeline
| Hour | Milestone |
|---|---|
| H0-1 | Contracts & setup |
| H1-6 | Work in parallel against mocks/recordings |
| **H6** | **Integration 1:** one real camera → dot on map |
| H6-12 | Multi-cam, Kalman, viewer pose |
| **H12** | **Integration 2:** fused tracks + prediction cone in the blind spot |
| H12-18 | Occlusion + ghost overlays |
| **H18** | **Core freeze, commit** |
| H18-21 | Stretch: cutout → skeleton → wall-constrained prediction |
| H21-23 | Rehearse 5x + record backup video |
| H23-24 | Devpost + pitch |

Sleep in shifts: A+B first, then C+D.

## Checklist
- [ ] Printed markers + chessboard; scale bars measured at 100 mm
- [ ] Corner of the room secured with the organisers
- [ ] `tools/make_certs.sh` re-run on the venue network; phones accept the cert
- [ ] `tools/list_cameras.py` sees every USB camera
- [ ] Contracts agreed (docs/CONTRACTS.md)
- [ ] Mock feed running before the H1 split
- [ ] Integration 1 (H6) / Integration 2 (H12) / freeze (H18)
- [ ] Backup video recorded
- [ ] Devpost submitted

## Risks / fallbacks
| Risk | Fallback |
|---|---|
| Laggy streams | USB webcams + our own travel router |
| Viewer jitter | Bigger wall markers, 5-frame smoothing, or a tethered USB "viewer" cam |
| ID swaps | Demo with one person in a bright top |
| AR not ready by H18 | Laptop showing the viewer cam + overlay |

## Pitch framing
Shared situational awareness for **firefighters / search & rescue**, with defence as a footnote.
Do **not** call it "Lattice" or use Anduril branding anywhere in the product.

## Prizes to stack
Theme prize, RS Group (hardware), Arm (on-device), Gemini (natural-language queries),
ElevenLabs (voice callouts), Hackiest Hack.
