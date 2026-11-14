// One wireframe person box + label per track. Style by state:
//   visible   (line of sight clear)      -> cyan solid box
//   hidden    (a wall blocks my view)    -> orange solid box, drawn through walls (depthTest off)
//   predicted (no camera sees it)        -> yellow dashed, pulsing box + 2-sigma floor ellipse
import * as THREE from 'three';
import { COLORS, PERSON, NEAR_SKIP_M, EXTRAP_CAP_S, SMOOTH_TAU_S } from './config.js';
import { mapRoot, mapToThree } from './frames.js';
import { blockingWall } from './los.js';
import { CanvasText, css } from './label.js';

const group = new THREE.Group();
group.name = 'tracks';
mapRoot.add(group);

// shared geometry
const boxGeom = new THREE.EdgesGeometry(new THREE.BoxGeometry(PERSON.w, PERSON.h, PERSON.d));
boxGeom.translate(0, PERSON.h / 2, 0);
const circleGeom = (() => {
  const pts = [];
  for (let i = 0; i < 48; i++) {
    const a = (i / 48) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a), 0.01, -Math.sin(a))); // unit circle on the floor, map-CCW
  }
  return new THREE.BufferGeometry().setFromPoints(pts);
})();

class TrackView {
  constructor(id) {
    this.id = id;
    this.root = new THREE.Group();
    this.solidMat = new THREE.LineBasicMaterial({ color: COLORS.visible, transparent: true });
    this.dashMat = new THREE.LineDashedMaterial({ color: COLORS.predicted, dashSize: 0.12, gapSize: 0.08, transparent: true });
    this.box = new THREE.LineSegments(boxGeom, this.solidMat);
    this.box.computeLineDistances();
    this.root.add(this.box);

    this.ellipse = new THREE.LineLoop(circleGeom, new THREE.LineBasicMaterial({ color: COLORS.predicted, transparent: true }));
    this.ellipse.visible = false;
    group.add(this.ellipse); // in map frame, not under root (has its own scale/rotation)

    this.text = new CanvasText(512, 128);
    this.label = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.text.texture, depthTest: false, transparent: true }));
    this.label.scale.set(0.5, 0.125, 1);
    this.label.position.y = PERSON.h + 0.2;
    this.label.renderOrder = 20;
    this.root.add(this.label);
    group.add(this.root);

    this.disp = null;       // smoothed displayed map position
    this.msg = null;        // latest track message
    this.recv = 0;          // local time (s) it arrived
    this.blocking = -1;
  }

  setMsg(msg, recv) { this.msg = msg; this.recv = recv; }

  update(now, dt, head, walls, serverOffset) {
    const m = this.msg;
    const age = Math.min(now - this.recv, EXTRAP_CAP_S);
    const target = [m.pos[0] + m.vel[0] * age, m.pos[1] + m.vel[1] * age];
    if (!this.disp) this.disp = target.slice();
    const k = 1 - Math.exp(-dt / SMOOTH_TAU_S);
    this.disp[0] += (target[0] - this.disp[0]) * k;
    this.disp[1] += (target[1] - this.disp[1]) * k;

    const near = head && Math.hypot(this.disp[0] - head[0], this.disp[1] - head[1]) < NEAR_SKIP_M;
    this.root.visible = !near;
    mapToThree(this.disp, 0, this.root.position);

    const predicted = m.state === 'predicted';
    this.blocking = head ? blockingWall(head, this.disp, walls) : -1;
    const hidden = this.blocking >= 0;

    const color = predicted ? COLORS.predicted : hidden ? COLORS.hidden : COLORS.visible;
    const mat = predicted ? this.dashMat : this.solidMat;
    mat.color.setHex(color);
    mat.depthTest = !hidden;                  // hidden boxes show through the (real) wall
    mat.opacity = predicted ? 0.55 + 0.45 * Math.sin(now * 6) : 1;
    this.box.material = mat;
    this.box.renderOrder = hidden ? 10 : 0;

    // label
    const lines = [`#${m.id}  ${predicted ? 'PREDICTED' : hidden ? 'HIDDEN' : 'VISIBLE'}`];
    if (predicted) lines.push(`last seen ${(now + serverOffset - m.last_seen).toFixed(1)} s`);
    else lines.push(hidden ? `via ${m.seen_by.join(',') || '?'}` : (m.seen_by.join(',') || ''));
    this.text.draw(lines, css(color));

    // 2-sigma ellipse from cov while predicted
    this.ellipse.visible = predicted && !near;
    if (this.ellipse.visible) {
      const [[a, b], [, c]] = m.cov;
      const mid = (a + c) / 2, rad = Math.sqrt(((a - c) / 2) ** 2 + b * b);
      const l1 = Math.max(mid + rad, 1e-6), l2 = Math.max(mid - rad, 1e-6);
      mapToThree(this.disp, 0, this.ellipse.position);
      this.ellipse.rotation.y = 0.5 * Math.atan2(2 * b, a - c);
      this.ellipse.scale.set(2 * Math.sqrt(l1), 1, 2 * Math.sqrt(l2));
      this.ellipse.material.opacity = mat.opacity;
    }
  }

  dispose() {
    group.remove(this.root, this.ellipse);
    this.solidMat.dispose(); this.dashMat.dispose(); this.ellipse.material.dispose();
    this.label.material.dispose(); this.text.texture.dispose();
  }
}

const views = new Map();
let lastMsgLocal = 0;
let serverOffset = 0; // server clock - local clock (s), for "last seen" ages

export function onTracksMsg(msg) {
  const now = performance.now() / 1000;
  lastMsgLocal = now;
  serverOffset = msg.t - now;
  const ids = new Set();
  for (const tr of msg.tracks) {
    ids.add(tr.id);
    if (!views.has(tr.id)) views.set(tr.id, new TrackView(tr.id));
    views.get(tr.id).setMsg(tr, now);
  }
  for (const [id, v] of views) if (!ids.has(id)) { v.dispose(); views.delete(id); }
}

export function updateTracks(dt, head, walls) {
  const now = performance.now() / 1000;
  for (const v of views.values()) v.update(now, dt, head, walls, serverOffset);
}

export const trackStats = () => ({
  count: views.size,
  age: lastMsgLocal ? performance.now() / 1000 - lastMsgLocal : Infinity,
});
