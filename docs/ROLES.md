# Steps per role (do them in order; each has a "done when")

## Role 1 — Meta Quest (quest/)
1. Quest setup (from Mac-1, not Windows): `brew install android-platform-tools` on Mac-1 (Meta Quest
   Developer Hub for macOS is optional). Enable Developer Mode in the Meta Horizon iPhone app → connect
   the Quest to Mac-1 with a USB-C DATA cable → accept "Allow USB debugging" (always allow) inside the
   headset → `adb devices` lists it. Join the Quest to the team network (venue Wi-Fi or iPhone hotspot).
   Debugging: Chrome on Mac-1 → chrome://inspect shows the Quest browser's tabs + console.
   Quest Link / Air Link are NOT needed.
   Done when: `adb devices` lists the Quest.
2. Passthrough smoke test: quest/index.html with three.js (ES module from a CDN or vendored),
   "Enter AR" button → navigator.xr.requestSession('immersive-ar') → floating cyan cube.
   Serve it from Mac-1's HTTPS server; open it once in the Quest browser and accept the cert.
   During development, skip the cert: run tools/quest_usb.sh on Mac-1 (adb reverse tcp:8001) and
   open http://localhost:8001/quest/ (a secure context; the server serves plain HTTP on 127.0.0.1:8001).
   `tools/quest_usb.sh --wireless` drops the cable (adb tcpip 5555 + adb connect; same network only).
   Done when: the cube floats over greyscale passthrough. (If immersive-ar is unsupported,
   switch to immersive-vr with a room replica and tell the team.)
3. Mock data: until Role 2's server is up, use a local JS mock (one person walking a loop).
   Then connect to wss://<server>/tracks and GET /world.
4. Alignment: touch the 4 corner markers in order 1 → 7 → 3 → 5 (round the square; config/markers.json order),
   least-squares rotation + offset from Quest local space to world space. Saved in localStorage; re-align any
   time (hold A+B 1 s). Touching in a different order rotates everything.
   Done when: a virtual marker drawn at world (0,0) sits on the real marker within ~10 cm.
5. Rendering (world → Quest local space):
   - walls: faint outline boxes
   - each track: compute line of sight from the headset's floor position to the track,
     tested against the walls
     - visible: thin outline capsule
     - hidden: glowing cyan capsule, label "via <seen_by>", wall segment highlighted
     - predicted: translucent orange capsule + floor fan sized by the covariance, "last seen Ns"
   Done when: walking around, the ghosts stay locked to their world positions.
6. Polish and demo: test casting (oculus.com/casting in a Mac browser, or the Meta Horizon iPhone app), HUD text (contacts count), and
   optional distance callouts.
7. iPhone fallback viewer (web/viewer/): 2D AR overlay = getUserMedia camera + canvas overlay from
   the server. NOT WebXR (iOS Safari doesn't support it). iPhones need a trusted cert for wss://:
   the tunnel URL or mkcert's root CA (tools/iphone_cert_help.sh).
   Done when: an iPhone shows a track drawn over its live camera view.
8. Stretch: skeleton ghosts if Role 2 sends 3D keypoints.

## Role 2 — Detection + 2D map (perception/, fusion/, web/map/)
1. In the first 30 min: fusion/mock_feed.py publishes fake tracks on /tracks so Roles 1 and 3
   are unblocked. FastAPI app in fusion/server.py: /ingest (WS), /tracks (WS), /world (GET),
   HTTPS on :8000 using certs/, plus plain HTTP on 127.0.0.1:8001 for the Quest (adb reverse).
   Done when: a browser receives mock tracks.
2. Detection: perception/detect.py runs YOLO11n person detection on frames from Role 3's
   capture module (or from recordings/ clips). Output the foot point (bottom-centre of
   the box) plus confidence.
3. Floor projection: perception/floor_project.py: pixel → floor point using the camera's
   K, dist, R, t from config/cameras.json (undistort, ray from the camera centre, intersect z=0).
   Done when: standing on a marker gives a floor position within ~15 cm of its known position.
4. Camera worker: perception/worker.py <cam_name>: capture → detect → project → send
   detections to /ingest. Must run on Mac-1, Mac-2 and the UNO Q (optionally the Windows PC).
5. Fusion: fusion/associate.py (Hungarian, 0.7 m gate) + fusion/tracker.py (constant-velocity
   Kalman, x=[x,y,vx,vy]; predict every tick, update on detection; state=predicted after
   0.5 s unseen; drop after 10 s). The PIR zone becomes a loose position measurement
   (zone centroid, large covariance).
6. Command map: web/map/index.html on a canvas: walls, camera FOV wedges, tracks
   (solid / dashed), covariance ellipses, the Quest's position if it reports it. Shown on the
   Windows PC's big screen if we use it.
7. Stretch: keep predictions from crossing walls; MediaPipe 3D keypoints in the world frame.

## Role 3 — Cameras, hardware, lead (calib/, edge/, config/, tools/, docs/)
1. Run tools/net_check.py on venue Wi-Fi. If it fails, set up an iPhone Personal Hotspot ("Maximize Compatibility" if a device can't join), join all
   devices, run make_certs.sh, update config/server.json. Have tools/tunnel.sh tested as backup. iPhones: tunnel URL or mkcert CA (tools/iphone_cert_help.sh).
   Done when: net_check passes from Mac-2, the Quest browser and the UNO Q.
2. Cameras: run tools/list_cameras.py on each machine. Choose cam A (Mac-1 USB), cam B
   (UNO Q USB, behind the wall), cam C (Mac-2 USB cam, or an iPhone via Continuity Camera:
   landscape, locked, near the Mac; it shifts OpenCV indices, so choose cameras by name/resolution
   from config/cameras.json). calib/capture.py: open a camera by NAME
   (matching resolution/order from config), timestamped frames, auto-reconnect.
3. Record 2–3 min of clips from each camera into recordings/ and AirDrop them to Role 2.
4. Lens calibration: calib/intrinsics.py with the chessboard (~15 views per camera)
   → K and dist into config/cameras.json.
5. Physical setup: lay out the demo area, place corner markers 1, 3, 5, 7 (ID 1 = origin; measure them into config/markers.json), build cardboard walls,
   measure them into config/walls.json, mount and tape the cameras, design the blind spot.
   Then calib/extrinsics.py: solvePnP on the corner markers → R, t per camera.
   Done when: reprojected marker corners land within a few pixels of the detected ones.
6. UNO Q (edge/): App Lab first-time setup over USB (Wi-Fi + password), SSH in, USB camera
   via a powered hub, run Role 2's worker as camB (or stream frames to Mac-1 if under 2 fps).
   MCU sketch: PIR on a 3.3V-safe pin → zone message via the Bridge → Python → /ingest;
   LED matrix radar from the /tracks WebSocket (one dot per track, blink if predicted);
   buzzer for proximity alerts.
7. Lead duties: run the checkpoints, merge any contract changes, keep main working,
   tag v1-demo at 07:00, product name, Devpost, demo script, backup video, pitch.
