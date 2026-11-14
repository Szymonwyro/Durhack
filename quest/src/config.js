// All tunables for the Quest viewer in one place.
export const VERSION = '0.1';

const q = new URLSearchParams(location.search);
export const MOCK = q.get('mock') === '1';
export const DEBUG = q.get('debug') === '1';

// Markers the wearer touches to align, map coords of their CENTRES (shape = INTERFACE.md align_points).
// Layout from config/markers.json: ID 1 = origin, ID 7 = (1.5,0), ID 3 = (1.5,1.5), ID 5 = (0,1.5).
// Add {marker:3,...} for a 3-point fit (and the 2.12 m diagonal).
export const ALIGN_POINTS = [
  { id: 'm1', marker: 1, pos: [0.0, 0.0] },
  { id: 'm7', marker: 7, pos: [1.5, 0.0] },
];
export const ALIGN_MAX_TIP_HEIGHT_M = 0.25; // reject samples not taken on the floor
export const ALIGN_WARN_RMS_M = 0.08;

// Quest passthrough is greyscale: bright colours only.
export const COLORS = {
  wall: 0x00e5ff,
  origin: 0xff8c00,
  alignPoint: 0xffffff,
  visible: 0x00e5ff,   // line of sight clear
  hidden: 0xff8c00,    // a wall is between me and the track (drawn through walls)
  predicted: 0xffd000, // no camera sees it
};

export const PERSON = { w: 0.6, d: 0.6, h: 1.75 };
export const NEAR_SKIP_M = 0.6;    // don't draw tracks this close to my head
export const EXTRAP_CAP_S = 0.5;   // max time to extrapolate pos with vel
export const SMOOTH_TAU_S = 0.12;  // exponential smoothing time constant
