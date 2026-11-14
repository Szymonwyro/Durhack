// One box + label per track, three styles:
//   in view      thin cyan wireframe                                  "#7 · 3.2 m"
//   behind wall  orange wireframe + light fill, drawn on top of all  "#7 · 4.1 m · behind wall"
//   predicted    dim pulsing dashed box, 2-sigma floor ellipse, heading path  "#7 · last seen 3 s ago"
// Positions are extrapolated with vel (cap 0.5 s) and smoothed; tracks that leave the stream, or all
// tracks when data stops, fade out over FADE_S.
import * as THREE from 'three';
import { COLORS, PERSON, NEAR_SKIP_M, EXTRAP_CAP_S, SMOOTH_TAU_S, FADE_S, HEADING_S } from './config.js';
import { mapRoot, mapToThree } from './frames.js';
import { blockingWall } from './los.js';
import { textSprite, css } from './label.js';

export const tracksGroup = new THREE.Group();
mapRoot.add(tracksGroup);

// shared geometry
const boxGeom = new THREE.EdgesGeometry(new THREE.BoxGeometry(PERSON.w, PERSON.h, PERSON.d));
boxGeom.translate(0, PERSON.h / 2, 0);
const fillGeom = new THREE.BoxGeometry(PERSON.w, PERSON.h, PERSON.d);
fillGeom.translate(0, PERSON.h / 2, 0);
const circleGeom = new THREE.BufferGeometry().setFromPoints(
  Array.from({ length: 48 }, (_, i) => {
    const a = (i / 48) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a), 0.01, -Math.sin(a)); // unit circle on the floor, map-CCW
  }));
let lineDistancesDone = false;

const clamp01 = (x) => Math.min(1, Math.max(0, x));

class TrackView {
  constructor() {
    this.root = new THREE.Group();
    this.lineMat = new THREE.LineBasicMaterial({ color: COLORS.visible, transparent: true });
    this.dashMat = new THREE.LineDashedMaterial({ color: COLORS.predicted, dashSize: 0.12, gapSize: 0.08, transparent: true });
    this.box = new THREE.LineSegments(boxGeom, this.lineMat);
    if (!lineDistancesDone) { this.box.computeLineDistances(); lineDistancesDone = true; }
    this.fill = new THREE.Mesh(fillGeom, new THREE.MeshBasicMaterial({
      color: COLORS.hidden, transparent: true, opacity: 0.15, depthTest: false, depthWrite: false,
    }));
    this.fill.renderOrder = 9;
    this.path = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]),
      new THREE.LineDashedMaterial({ color: COLORS.predicted, dashSize: 0.1, gapSize: 0.07, transparent: true }));
    const { text, sprite } = textSprite(512, 96, 0.64);
    this.text = text;
    this.label = sprite;
    this.label.position.y = PERSON.h + 0.18;
    this.root.add(this.box, this.fill, this.path, this.label);
    this.ellipse = new THREE.LineLoop(circleGeom, new THREE.LineBasicMaterial({ color: COLORS.predicted, transparent: true }));
    tracksGroup.add(this.root, this.ellipse); // ellipse has its own scale/rotation in the map frame

    this.disp = null;   // smoothed displayed map position
    this.msg = null;
    this.recv = 0;      // local time (s) the latest message arrived
    this.goneAt = null; // local time it left the stream
  }

  update(now, dt, head, walls, fade, serverNow) {
    const m = this.msg;
    const age = Math.min(now - this.recv, EXTRAP_CAP_S);
    const target = [m.pos[0] + m.vel[0] * age, m.pos[1] + m.vel[1] * age];
    if (!this.disp) this.disp = target.slice();
    const k = 1 - Math.exp(-dt / SMOOTH_TAU_S);
    this.disp[0] += (target[0] - this.disp[0]) * k;
    this.disp[1] += (target[1] - this.disp[1]) * k;

    let alpha = fade;
    if (this.goneAt !== null) alpha *= clamp01(1 - (now - this.goneAt) / FADE_S);
    const dist = head ? Math.hypot(this.disp[0] - head[0], this.disp[1] - head[1]) : null;
    const show = alpha > 0 && !(dist !== null && dist < NEAR_SKIP_M);
    this.root.visible = show;
    mapToThree(this.disp, 0, this.root.position);

    const predicted = m.state === 'predicted';
    const hidden = head ? blockingWall(head, this.disp, walls) >= 0 : false;

    let color;
    if (predicted) {
      color = COLORS.predicted;
      this.box.material = this.dashMat;
      this.dashMat.opacity = alpha * (0.3 + 0.35 * (0.5 + 0.5 * Math.sin(now * 5)));
    } else {
      color = hidden ? COLORS.hidden : COLORS.visible;
      this.box.material = this.lineMat;
      this.lineMat.opacity = alpha;
    }
    this.box.material.color.setHex(color);
    this.dot = { pos: this.disp, color, alpha, predicted, hidden }; // for the minimap
    this.box.material.depthTest = !hidden; // behind a wall: draw on top of everything
    this.box.renderOrder = hidden ? 10 : 0;
    this.fill.visible = hidden && !predicted;
    this.fill.material.opacity = 0.15 * alpha;

    const d = dist !== null ? ` · ${dist.toFixed(1)} m` : '';
    const label = predicted
      ? `#${m.id} · last seen ${Math.max(0, Math.round(serverNow - m.last_seen))} s ago`
      : `#${m.id}${d}${hidden ? ' · behind wall' : ''}`;
    this.text.draw([label], { color: css(color), center: true });
    this.label.material.opacity = alpha;

    // predicted: short dashed heading path along vel
    const speed = Math.hypot(m.vel[0], m.vel[1]);
    this.path.visible = predicted && speed > 0.05;
    if (this.path.visible) {
      const len = Math.min(speed * HEADING_S, 2.0);
      const pos = this.path.geometry.attributes.position;
      pos.setXYZ(0, 0, 0.03, 0);
      pos.setXYZ(1, (m.vel[0] / speed) * len, 0.03, -(m.vel[1] / speed) * len);
      pos.needsUpdate = true;
      this.path.computeLineDistances();
      this.path.material.opacity = this.dashMat.opacity;
    }

    // predicted: 2-sigma ellipse from cov
    this.ellipse.visible = predicted && show;
    if (this.ellipse.visible) {
      const [[a, b], [, c]] = m.cov;
      const mid = (a + c) / 2, rad = Math.sqrt(((a - c) / 2) ** 2 + b * b);
      mapToThree(this.disp, 0, this.ellipse.position);
      this.ellipse.rotation.y = 0.5 * Math.atan2(2 * b, a - c);
      this.ellipse.scale.set(2 * Math.sqrt(Math.max(mid + rad, 1e-6)), 1, 2 * Math.sqrt(Math.max(mid - rad, 1e-6)));
      this.ellipse.material.opacity = alpha * 0.8;
    }
  }

  dispose() {
    tracksGroup.remove(this.root, this.ellipse);
    this.lineMat.dispose(); this.dashMat.dispose(); this.fill.material.dispose();
    this.path.geometry.dispose(); this.path.material.dispose(); this.ellipse.material.dispose();
    this.label.material.dispose(); this.text.texture.dispose();
  }
}

const views = new Map();

export function onTracksMsg(msg) {
  const now = performance.now() / 1000;
  const ids = new Set();
  for (const tr of msg.tracks) {
    ids.add(tr.id);
    let v = views.get(tr.id);
    if (!v) views.set(tr.id, (v = new TrackView()));
    v.msg = tr; v.recv = now; v.goneAt = null;
  }
  for (const [id, v] of views) if (!ids.has(id) && v.goneAt === null) v.goneAt = now;
}

// fade: 0..1 from data freshness; at 0 everything is cleared (idle state)
export function updateTracks(dt, head, walls, fade, serverNow) {
  const now = performance.now() / 1000;
  for (const [id, v] of views) {
    if (fade <= 0 || (v.goneAt !== null && now - v.goneAt > FADE_S)) { v.dispose(); views.delete(id); continue; }
    v.update(now, dt, head, walls, fade, serverNow);
  }
}

export const trackCount = () => views.size;
export const trackDots = () => [...views.values()].filter((v) => v.dot && v.dot.alpha > 0).map((v) => v.dot);
