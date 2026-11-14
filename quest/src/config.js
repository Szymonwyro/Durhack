// All tunables for the Quest viewer in one place.
export const VERSION = '0.3';

const q = new URLSearchParams(location.search);
export const MOCK = q.get('mock') === '1';
export const DEBUG = q.get('debug') === '1';

// Fallback calibration corners, used until /world provides align_points (same shape as INTERFACE.md).
// Map positions of the marker CENTRES, in touch order. Printed IDs as laid out (config/markers.json):
// ID 1 = origin (0,0), ID 7 = (1.5,0), ID 3 = (1.5,1.5), ID 5 = (0,1.5).
export const FALLBACK_ALIGN_POINTS = [
  { id: 'c0', marker: 1, pos: [0.0, 0.0] },
  { id: 'c1', marker: 7, pos: [1.5, 0.0] },
  { id: 'c2', marker: 3, pos: [1.5, 1.5] },
  { id: 'c3', marker: 5, pos: [0.0, 1.5] },
];
export const ALIGN_MAX_TIP_HEIGHT_M = 0.3; // a sample higher than this wasn't taken on the floor
export const ALIGN_WARN_M = 0.08;          // per-marker error that triggers "B to redo it"
export const COMBO_HOLD_S = 1.0;           // hold A + B together this long to recalibrate (any time)

// Quest passthrough is greyscale: bright colours only.
export const COLORS = {
  visible: 0x00e5ff,   // in direct view
  hidden: 0xff8a00,    // behind a real wall (drawn on top)
  predicted: 0xffb347, // no camera sees them (dim, pulsing, dashed)
  corner: 0x00e5ff,
  wall: 0x00e5ff,      // debug only
  zone: 0xff8a00,
  object: { exit: 0xffffff, door: 0x00e5ff, hazard: 0xff8a00, furniture: 0xaaaaaa, other: 0xaaaaaa },
};

export const PERSON = { w: 0.6, d: 0.6, h: 1.75 };
export const NEAR_SKIP_M = 0.6;     // no box on the wearer
export const EXTRAP_CAP_S = 0.5;    // max extrapolation with vel
export const SMOOTH_TAU_S = 0.12;   // display smoothing time constant
export const HEADING_S = 1.5;       // predicted heading path length = speed * this (max 2 m)
export const DATA_TIMEOUT_S = 1.0;  // no /tracks for this long -> fade out
export const FADE_S = 1.0;          // fade duration
export const RECONNECT_S = 2.0;
