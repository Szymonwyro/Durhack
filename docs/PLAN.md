# Plan

The source of truth is [CLAUDE.md](../CLAUDE.md), which covers the project, hardware, devices, contracts and checkpoints. Step-by-step work for each role is in [ROLES.md](ROLES.md). This file covers the checklist, risks and pitch.

## Roles (summary)
- **Role 1: Meta Quest** (`quest/`). WebXR immersive-ar viewer, two-point alignment, ghost rendering.
- **Role 2: Detection + map** (`perception/`, `fusion/`, `web/map/`). Mock feed first, then YOLO → floor projection → camera worker → Hungarian + Kalman fusion → command map.
- **Role 3: Cameras, hardware, lead** (`calib/`, `edge/`, `config/`, `tools/`, `docs/`). Network, cameras, calibration, physical setup, UNO Q (camB + PIR + LED radar), checkpoints, pitch.

## Checkpoints (Sat → Sun; adjust to the real deadline)
| Time | Milestone |
|---|---|
| 16:00 | One real camera → dot on map; Quest shows a mock track at the right spot after alignment |
| 19:00 | Cams A+B fused, Kalman prediction in blind spot, UNO Q sending detections |
| 23:00 | Quest shows a ghost through the real wall from a real camera; PIR shrinks prediction; LED radar |
| **07:00** | **Core frozen, tag `v1-demo`** |
| 09:00+ | Rehearse 5x, backup video, Devpost |

Sleep in shifts, and keep at least one person who knows the server awake at all times.

## Checklist
- [ ] `tools/net_check.py` run on venue Wi-Fi; if it fails, phone hotspot up and all devices joined
- [ ] `tools/make_certs.sh` re-run on that network; `config/server.json` written; every device opens `https://<mac1>:8000`
- [ ] Printed markers + chessboard; scale bars measured at 100 mm
- [ ] Corner of the room secured with the organisers
- [ ] Cameras identified by name in `config/cameras.json` (camA / camB / camC)
- [ ] Mock feed on /tracks within the first 30 min
- [ ] Checkpoints 16:00 / 19:00 / 23:00 met; `v1-demo` tagged at 07:00
- [ ] Backup video recorded
- [ ] Devpost submitted

## Risks / fallbacks
| Risk | Fallback |
|---|---|
| Quest passthrough fails | Quest shows a VR replica of the room; a phone becomes the AR viewer |
| UNO Q too slow for detection (<2 fps) | It streams frames to Mac-1 for detection and keeps the PIR + LEDs |
| ID swaps between people | Demo with one person in a bright top |
| Venue Wi-Fi blocks device-to-device traffic | Phone hotspot (≈5 devices; keep it charging) → `tools/tunnel.sh` |
| Laggy streams | USB cameras; send detections, not video, over the network |
| Alignment drift on the Quest | "Re-align" button; bigger markers |

## Pitch framing
Shared situational awareness for **firefighters / search & rescue**, with defence as a footnote.
Do **not** use "Lattice" or "Anduril" anywhere in the product.

## Prizes to stack
Theme prize, RS Group / Arduino (hardware: UNO Q, PIR, LED radar), Arm (on-device detection on the UNO Q),
Meta / XR if offered, Gemini (natural-language queries), ElevenLabs (voice callouts), Hackiest Hack.
