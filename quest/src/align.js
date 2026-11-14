// Align the map to the room: press A (right controller), then pull the trigger with the controller tip
// on each ALIGN_POINT in order, touching the floor (point 1 = corner marker ID 1 centre = map 0,0).
// Solves a 2D rigid transform (least squares, >= 2 points) -> mapRoot yaw about Y + position (floor offset).
import * as THREE from 'three';
import { ALIGN_POINTS, ALIGN_MAX_TIP_HEIGHT_M, ALIGN_WARN_RMS_M } from './config.js';
import { mapRoot } from './frames.js';

const KEY = 'quest-align-v2'; // v1 entries could be taken off the floor; ignore them
const BTN_TRIGGER = 0, BTN_A = 4; // xr-standard gamepad mapping on Quest Touch

let state = 'none';   // none | saved | ok | aligning | lost
let samples = [];
let rmsM = null;
let right = null;      // { ctrl, source }
const prev = { a: false, trig: false };
let rejectUntil = 0;   // show the 'touch the floor' warning until this time (ms)

// small sphere at the controller's target-ray origin = the point we sample
const tip = new THREE.Mesh(new THREE.SphereGeometry(0.012, 12, 8), new THREE.MeshBasicMaterial({ color: 0xff8c00 }));

function apply({ theta, x, y, z }) {
  mapRoot.rotation.set(0, theta, 0);
  mapRoot.position.set(x, y, z);
  mapRoot.updateMatrixWorld(true);
}

export function loadAlignment() {
  try {
    const a = JSON.parse(localStorage.getItem(KEY));
    if (a && Number.isFinite(a.theta)) { apply(a); rmsM = a.rms; state = 'saved'; return true; }
  } catch { /* no storage */ }
  return false;
}

// map points m[i] -> measured world points w[i] (both 2D, world 2D = (x, -z) so rotation sign matches)
function solve(m, w) {
  const n = m.length;
  const cm = [0, 0], cw = [0, 0];
  for (let i = 0; i < n; i++) { cm[0] += m[i][0] / n; cm[1] += m[i][1] / n; cw[0] += w[i][0] / n; cw[1] += w[i][1] / n; }
  let sc = 0, sd = 0;
  for (let i = 0; i < n; i++) {
    const ax = m[i][0] - cm[0], ay = m[i][1] - cm[1];
    const bx = w[i][0] - cw[0], by = w[i][1] - cw[1];
    sd += ax * bx + ay * by;
    sc += ax * by - ay * bx;
  }
  const theta = Math.atan2(sc, sd);
  const cs = Math.cos(theta), sn = Math.sin(theta);
  const t = [cw[0] - (cs * cm[0] - sn * cm[1]), cw[1] - (sn * cm[0] + cs * cm[1])];
  let err = 0;
  for (let i = 0; i < n; i++) {
    const px = cs * m[i][0] - sn * m[i][1] + t[0], py = sn * m[i][0] + cs * m[i][1] + t[1];
    err += (px - w[i][0]) ** 2 + (py - w[i][1]) ** 2;
  }
  return { theta, t, rms: Math.sqrt(err / n) };
}

function finish() {
  const m = ALIGN_POINTS.map((ap) => ap.pos);
  const w = samples.map((p) => [p.x, -p.z]);
  const { theta, t, rms } = solve(m, w);
  const floorY = samples.reduce((s, p) => s + p.y, 0) / samples.length;
  const a = { theta, x: t[0], y: floorY, z: -t[1], rms, t: Date.now() };
  apply(a);
  rmsM = rms;
  try { localStorage.setItem(KEY, JSON.stringify(a)); } catch { /* ignore */ }
  state = 'ok';
  console.log('[align] solved', JSON.stringify(a));
}

export function initAlign(renderer, scene) {
  for (let i = 0; i < 2; i++) {
    const ctrl = renderer.xr.getController(i);
    scene.add(ctrl);
    ctrl.addEventListener('connected', (e) => {
      if (e.data.handedness === 'right' && e.data.gamepad) { right = { ctrl, source: e.data }; ctrl.add(tip); }
    });
    ctrl.addEventListener('disconnected', () => { if (right && right.ctrl === ctrl) right = null; });
  }
  renderer.xr.addEventListener('sessionstart', () => {
    const space = renderer.xr.getReferenceSpace();
    space.addEventListener('reset', () => { state = 'lost'; console.warn('[align] reference space reset'); });
  });
}

export function updateAlign() {
  if (!right) return;
  const b = right.source.gamepad.buttons;
  const a = !!(b[BTN_A] && b[BTN_A].pressed), trig = !!(b[BTN_TRIGGER] && b[BTN_TRIGGER].pressed);
  if (a && !prev.a) { state = 'aligning'; samples = []; }
  if (trig && !prev.trig && state === 'aligning') {
    const p = new THREE.Vector3();
    right.ctrl.getWorldPosition(p);
    console.log('[align] sample', samples.length + 1, JSON.stringify(p.toArray().map((x) => +x.toFixed(3))));
    if (p.y > ALIGN_MAX_TIP_HEIGHT_M) {      // not on the floor: reject, ask again
      rejectUntil = performance.now() + 2500;
      console.warn(`[align] rejected: tip ${p.y.toFixed(2)} m above floor`);
    } else {
      samples.push(p);
    }
    if (samples.length === ALIGN_POINTS.length) finish();
  }
  prev.a = a; prev.trig = trig;
}

export function alignStatus() {
  const err = rmsM != null ? ` (err ${(rmsM * 100).toFixed(1)} cm)` : '';
  switch (state) {
    case 'ok': return rmsM > ALIGN_WARN_RMS_M
      ? { text: 'aligned' + err, warn: `Aligned but error ${(rmsM * 100).toFixed(0)} cm - press A to redo` }
      : { text: 'aligned' + err };
    case 'saved': return { text: 'loaded from storage' + err };
    case 'lost': return { text: 'LOST', warn: 'ALIGNMENT LOST - press A to re-align' };
    case 'aligning': {
      const i = samples.length, ap = ALIGN_POINTS[i];
      const warn = performance.now() < rejectUntil ? 'Too high - touch the FLOOR with the controller tip' : '';
      return {
        text: `aligning ${i}/${ALIGN_POINTS.length}`, warn,
        prompt: `Touch marker ${ap.marker} centre on the floor, pull trigger`,
      };
    }
    default: return { text: 'not aligned', prompt: right ? 'Press A to align' : '' };
  }
}
