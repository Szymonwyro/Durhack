"""Per-camera extrinsics (R, t) in the world frame from ArUco markers (ID 0 = origin).

Owner: A (Calibration & cameras)
PREP STUB - no implementation. Write during the event.
See docs/CONTRACTS.md for frames and message schemas.

TODO:
  - detect DICT_4X4_50 markers (180 mm) in each fixed camera
  - solve camera pose relative to world frame
  - write R/t into config/cameras.json; sanity-check by reprojecting known marker corners
"""
