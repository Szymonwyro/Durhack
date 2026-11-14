// Calibration by touching the corner markers (the only alignment method; the headset never sends its pose).
//   start  -> "Calibrate: touch the 4 corner markers · Press A to start"
//   calib  -> "Touch marker N · pull trigger" per corner (B = undo last)
//   review -> "Aligned · error 3 cm", per-marker errors; > 8 cm: "Marker N is 11 cm off · B to redo it"; A = accept
//   redo   -> re-touch one marker (B = cancel)
//   aligned (saved per world_version) | lost (reference-space reset: "press A to recalibrate")
// Hold A for 2 s at any time to restart. Fit: least-squares 2D rigid transform (yaw + x/y offset).
import * as THREE from 'three';
import { FALLBACK_ALIGN_POINTS, ALIGN_MAX_TIP_HEIGHT_M, ALIGN_WARN_M, HOLD_RESTART_S, COLORS } from './config.js';
import { mapRoot } from './frames.js';
import { CalibPanel } from './calibpanel.js';

const BTN_TRIGGER = 0, BTN_A = 4, BTN_B = 5; // xr-standard mapping, Quest Touch right controller
const storeKey = (v) => `quest-align:${v}`;
const keyOf = (pts) => JSON.stringify(pts.map((p) => p.pos));
const cm = (m) => `${Math.round(m * 100)} cm`;

let points = FALLBACK_ALIGN_POINTS, apKey = keyOf(points), worldVersion = 0;
let state = 'start';
let samples = [];   // per corner: THREE.Vector3 on the floor (headset frame) or null
let next = 0, redoIdx = -1;
let fit = null;     // { theta, x, z, rms, errs[] }
let right = null;   // { ctrl, source }
const prev = { a: false, b: false, trig: false };
let aDownAt = null, holdFired = false;
let note = '', noteUntil = 0;
let panel = null;

const crosses = new THREE.Group(); // cyan crosses on recorded spots (headset frame, not mapRoot)

// glowing sphere on the controller tip = the point that will be recorded
const tip = new THREE.Group();
tip.add(new THREE.Mesh(new THREE.SphereGeometry(0.008, 12, 8), new THREE.MeshBasicMaterial({ color: 0xffffff })));
tip.add(new THREE.Mesh(new THREE.SphereGeometry(0.02, 16, 10), new THREE.MeshBasicMaterial({
  color: COLORS.visible, transparent: true, opacity: 0.5, depthWrite: false, blending: THREE.AdditiveBlending,
})));

const label = (i) => String(points[i].marker ?? i);

function rebuildCrosses() {
  for (const c of [...crosses.children]) { crosses.remove(c); c.geometry.dispose(); c.material.dispose(); }
  const r = 0.08, y = 0.004, p = [];
  for (const s of samples) {
    if (!s) continue;
    p.push(new THREE.Vector3(s.x - r, y, s.z), new THREE.Vector3(s.x + r, y, s.z),
      new THREE.Vector3(s.x, y, s.z - r), new THREE.Vector3(s.x, y, s.z + r));
  }
  if (p.length) crosses.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(p),
    new THREE.LineBasicMaterial({ color: COLORS.visible })));
}

function applyFit(f) {
  mapRoot.rotation.set(0, f.theta, 0);
  mapRoot.position.set(f.x, 0, f.z);
  mapRoot.updateMatrixWorld(true);
}

// least-squares rigid fit of map points m[i] onto measured floor points w[i] (w = headset (x, -z))
export function solve(m, w) {
  const n = m.length, cm_ = [0, 0], cw = [0, 0];
  for (let i = 0; i < n; i++) { cm_[0] += m[i][0] / n; cm_[1] += m[i][1] / n; cw[0] += w[i][0] / n; cw[1] += w[i][1] / n; }
  let sc = 0, sd = 0;
  for (let i = 0; i < n; i++) {
    const ax = m[i][0] - cm_[0], ay = m[i][1] - cm_[1], bx = w[i][0] - cw[0], by = w[i][1] - cw[1];
    sd += ax * bx + ay * by;
    sc += ax * by - ay * bx;
  }
  const theta = Math.atan2(sc, sd), cs = Math.cos(theta), sn = Math.sin(theta);
  const t = [cw[0] - (cs * cm_[0] - sn * cm_[1]), cw[1] - (sn * cm_[0] + cs * cm_[1])];
  const errs = m.map(([x, y], i) => Math.hypot(cs * x - sn * y + t[0] - w[i][0], sn * x + cs * y + t[1] - w[i][1]));
  const rms = Math.sqrt(errs.reduce((s, e) => s + e * e, 0) / n);
  return { theta, x: t[0], z: -t[1], rms, errs };
}

function save() { try { localStorage.setItem(storeKey(worldVersion), JSON.stringify({ ...fit, apKey })); } catch { /* no storage */ } }
function forget() { try { localStorage.removeItem(storeKey(worldVersion)); } catch { /* no storage */ } }
function tryLoad() {
  try {
    const s = JSON.parse(localStorage.getItem(storeKey(worldVersion)));
    if (s && s.apKey === apKey && Number.isFinite(s.theta)) {
      fit = s; applyFit(s); state = 'aligned';
      console.log('[align] loaded for world_version', worldVersion, 'error', cm(s.rms));
      return true;
    }
  } catch { /* no storage */ }
  return false;
}

// called with every (validated) world, and once at start with an empty world (fallback points)
export function setWorld(world, { alignChanged = false } = {}) {
  const pts = world.align_points && world.align_points.length >= 2 ? world.align_points : FALLBACK_ALIGN_POINTS;
  const k = keyOf(pts), changed = k !== apKey;
  points = pts; apKey = k; worldVersion = world.world_version ?? 0;
  if ((alignChanged || changed) && state !== 'start') {
    console.warn('[align] align_points changed - recalibration required');
    state = 'start'; samples = []; fit = null; rebuildCrosses();
    return;
  }
  if (state === 'aligned') save();      // same points, new world_version: keep the alignment
  else if (state === 'start') tryLoad();
}

export const getPoints = () => points;
export const getState = () => state;
export const getFit = () => fit;

function showNote(s) { note = s; noteUntil = performance.now() + 2500; }

function pulse(strength = 0.6, ms = 60) {
  const g = right?.source.gamepad;
  try {
    if (g?.hapticActuators?.length) g.hapticActuators[0].pulse(strength, ms);
    else g?.vibrationActuator?.playEffect?.('dual-rumble', { duration: ms, strongMagnitude: strength, weakMagnitude: strength });
  } catch { /* no haptics */ }
}

function startCalib() {
  state = 'calib'; samples = points.map(() => null); next = 0; redoIdx = -1; fit = null; note = '';
  rebuildCrosses();
  console.log('[align] calibration started,', points.length, 'markers');
}

function record(i) {
  const p = new THREE.Vector3();
  right.ctrl.getWorldPosition(p);
  if (p.y > ALIGN_MAX_TIP_HEIGHT_M) {
    showNote(`Too high (${p.y.toFixed(2)} m) · touch the floor`);
    pulse(0.2, 30);
    console.warn('[align] rejected sample', p.y.toFixed(2), 'm above floor');
    return false;
  }
  p.y = 0; // projected to the floor
  samples[i] = p;
  rebuildCrosses();
  pulse();
  console.log('[align] marker', label(i), JSON.stringify([+p.x.toFixed(3), +p.z.toFixed(3)]));
  return true;
}

function review() {
  fit = solve(points.map((p) => p.pos), samples.map((s) => [s.x, -s.z]));
  applyFit(fit); // preview: corners jump to the fitted spots
  state = 'review';
  console.log('[align] fit', JSON.stringify({ rms_cm: +(fit.rms * 100).toFixed(1), errs_cm: fit.errs.map((e) => +(e * 100).toFixed(1)) }));
}

const worst = () => fit.errs.reduce((k, e, i, a) => (e > a[k] ? i : k), 0);

function onA() {
  if (state === 'start' || state === 'lost') startCalib();
  else if (state === 'review') {
    save(); state = 'aligned'; samples = []; rebuildCrosses();
    console.log('[align] accepted, error', cm(fit.rms));
  }
}

function onB() {
  if (state === 'calib' && next > 0) { next--; samples[next] = null; rebuildCrosses(); pulse(0.3, 30); }
  else if (state === 'review') { redoIdx = worst(); state = 'redo'; }
  else if (state === 'redo') state = 'review';
}

function onTrigger() {
  if (state === 'calib') { if (record(next) && ++next === points.length) review(); }
  else if (state === 'redo') { if (record(redoIdx)) review(); }
}

function onReset() {
  console.warn('[align] reference space reset (recenter)');
  if (state !== 'start') { forget(); state = 'lost'; samples = []; fit = null; rebuildCrosses(); }
}

export function initAlign(renderer, scene, camera) {
  scene.add(crosses);
  panel = new CalibPanel(camera);
  for (let i = 0; i < 2; i++) {
    const ctrl = renderer.xr.getController(i);
    scene.add(ctrl);
    ctrl.addEventListener('connected', (e) => {
      if (e.data.handedness === 'right' && e.data.gamepad) { right = { ctrl, source: e.data }; ctrl.add(tip); }
    });
    ctrl.addEventListener('disconnected', () => { if (right && right.ctrl === ctrl) { ctrl.remove(tip); right = null; } });
  }
  renderer.xr.addEventListener('sessionstart', () => {
    renderer.xr.getReferenceSpace().addEventListener('reset', onReset);
  });
}

function panelInfo() {
  const base = {
    visible: true,
    points: points.map((p) => p.pos),
    labels: points.map((_, i) => label(i)),
    done: points.map((_, i) => !!samples[i]),
    active: -1,
    bad: [],
  };
  const n = performance.now() < noteUntil ? note : '';
  switch (state) {
    case 'start':
      return { ...base, title: `Calibrate: touch the ${points.length} corner markers`, lines: ['Press A to start'], active: 0 };
    case 'calib':
      return { ...base, title: `Touch marker ${label(next)} · pull trigger`, warn: !!n, active: next,
        lines: [n ? '!' + n : `${next + 1} of ${points.length}${next > 0 ? ' · B = undo' : ''}`] };
    case 'redo':
      return { ...base, title: `Redo marker ${label(redoIdx)} · pull trigger`, warn: !!n, active: redoIdx,
        lines: [n ? '!' + n : 'B = cancel'] };
    case 'review': {
      const w = worst(), bad = fit.errs.map((e) => e > ALIGN_WARN_M);
      const lines = [fit.errs.map((e, i) => `${label(i)}: ${Math.round(e * 100)}`).join('  ') + ' cm'];
      if (bad[w]) lines.push(`!Marker ${label(w)} is ${cm(fit.errs[w])} off · B to redo it`);
      lines.push('A = accept');
      return { ...base, title: `Aligned · error ${cm(fit.rms)}`, lines, bad, warn: bad[w] };
    }
    case 'lost':
      return { ...base, title: 'Alignment lost · press A to recalibrate', lines: [], done: points.map(() => false) };
    default:
      return { visible: false };
  }
}

export function updateAlign(nowS, presenting) {
  if (right && presenting) {
    const b = right.source.gamepad.buttons, pressed = (i) => !!(b[i] && b[i].pressed);
    const a = pressed(BTN_A), bb = pressed(BTN_B), trig = pressed(BTN_TRIGGER);
    if (a && !prev.a) { aDownAt = nowS; holdFired = false; onA(); }
    if (a && !holdFired && aDownAt !== null && nowS - aDownAt >= HOLD_RESTART_S) { holdFired = true; startCalib(); pulse(0.8, 150); }
    if (!a) aDownAt = null;
    if (bb && !prev.b) onB();
    if (trig && !prev.trig) onTrigger();
    prev.a = a; prev.b = bb; prev.trig = trig;
  }
  tip.visible = state !== 'aligned';
  panel.update(presenting ? panelInfo() : { visible: false });
}
