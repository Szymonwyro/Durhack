// Pose skeleton for one track (INTERFACE.md 3a): bones as line segments + joint dots.
//   track.skeleton = { kp: [[x, y, z, conf] | null, ...17], src, age, frozen }   world metres, COCO-17 order
// Joints are drawn relative to the track's foot, inside the track's root group, so the skeleton follows the
// same smoothed, extrapolated position as the box. Joint motion is smoothed between server messages (~10 Hz).
// frozen (no camera sees the joints) -> dim + pulsing in the "predicted" colour. Absent field -> hidden.
// Turn off with ?skeleton=0.
import * as THREE from 'three';
import { COLORS } from './config.js';

export const SKELETON_ON = new URLSearchParams(location.search).get('skeleton') !== '0';
// COCO-17: 0 nose, 1-2 eyes, 3-4 ears, 5-6 shoulders, 7-8 elbows, 9-10 wrists, 11-12 hips, 13-14 knees, 15-16 ankles
const BONES = [[5, 7], [7, 9], [6, 8], [8, 10], [5, 6], [5, 11], [6, 12], [11, 12], [11, 13], [13, 15], [12, 14],
  [14, 16], [0, 5], [0, 6]];
const JOINTS = 17;
const SMOOTH_TAU_S = 0.08;   // joint smoothing between messages
const HEAD_R = 0.11;         // head ring radius (m), drawn at the nose

const headGeom = new THREE.BufferGeometry().setFromPoints(
  Array.from({ length: 20 }, (_, i) => {
    const a = (i / 20) * Math.PI * 2;
    return new THREE.Vector3(Math.cos(a) * HEAD_R, Math.sin(a) * HEAD_R, 0);
  }));

const okJoint = (j) => Array.isArray(j) && j.length >= 3 && Number.isFinite(j[0]) && Number.isFinite(j[1]) && Number.isFinite(j[2]);

export class SkeletonView {
  constructor() {
    this.object = new THREE.Group();
    this.mat = new THREE.LineBasicMaterial({ color: COLORS.visible, transparent: true });
    this.dotMat = new THREE.PointsMaterial({ color: COLORS.visible, size: 0.045, sizeAttenuation: true, transparent: true });
    this.bones = new THREE.LineSegments(new THREE.BufferGeometry().setAttribute('position',
      new THREE.Float32BufferAttribute(new Float32Array(BONES.length * 2 * 3), 3)), this.mat);
    this.dots = new THREE.Points(new THREE.BufferGeometry().setAttribute('position',
      new THREE.Float32BufferAttribute(new Float32Array(JOINTS * 3), 3)), this.dotMat);
    this.head = new THREE.LineLoop(headGeom, this.mat);
    this.bones.frustumCulled = this.dots.frustumCulled = this.head.frustumCulled = false;
    this.object.add(this.bones, this.dots, this.head);
    this.object.visible = false;
    this.cur = new Float32Array(JOINTS * 3);   // smoothed joints in root-local three coords (x, up, -y)
    this.have = new Array(JOINTS).fill(false);
    this.top = 0;                              // highest joint (m), for the label
  }

  // m: track message; headLocal: wearer's head in root-local three coords (or null), to face the head ring
  update(m, dt, alpha, color, hidden, headLocal) {
    const s = SKELETON_ON ? m.skeleton : null;
    const kp = s && Array.isArray(s.kp) && s.kp.length === JOINTS ? s.kp : null;
    if (!kp || alpha <= 0) { this.object.visible = false; this.have.fill(false); this.top = 0; return false; }

    const k = 1 - Math.exp(-dt / SMOOTH_TAU_S);
    let top = 0, any = false;
    for (let i = 0; i < JOINTS; i++) {
      const j = kp[i];
      if (!okJoint(j)) { this.have[i] = false; continue; }
      // map (x, y, z) relative to the track's foot -> three (dx, z, -dy)
      const tx = j[0] - m.pos[0], ty = j[2], tz = -(j[1] - m.pos[1]);
      const o = i * 3;
      if (!this.have[i]) { this.cur[o] = tx; this.cur[o + 1] = ty; this.cur[o + 2] = tz; } else {
        this.cur[o] += (tx - this.cur[o]) * k; this.cur[o + 1] += (ty - this.cur[o + 1]) * k; this.cur[o + 2] += (tz - this.cur[o + 2]) * k;
      }
      this.have[i] = true; any = true;
      if (this.cur[o + 1] > top) top = this.cur[o + 1];
    }
    if (!any) { this.object.visible = false; this.top = 0; return false; }

    // bones: a bone with a missing end collapses to a zero-length segment (not drawn)
    const bp = this.bones.geometry.attributes.position;
    BONES.forEach(([a, b], n) => {
      const ok = this.have[a] && this.have[b];
      const pa = ok ? a * 3 : 0, pb = ok ? b * 3 : 0;
      bp.setXYZ(n * 2, ok ? this.cur[pa] : 0, ok ? this.cur[pa + 1] : 0, ok ? this.cur[pa + 2] : 0);
      bp.setXYZ(n * 2 + 1, ok ? this.cur[pb] : 0, ok ? this.cur[pb + 1] : 0, ok ? this.cur[pb + 2] : 0);
    });
    bp.needsUpdate = true;
    const dp = this.dots.geometry.attributes.position;
    let n = 0;
    for (let i = 5; i < JOINTS; i++) {   // body joints only (face points would clutter)
      if (this.have[i]) { dp.setXYZ(n++, this.cur[i * 3], this.cur[i * 3 + 1], this.cur[i * 3 + 2]); }
    }
    this.dots.geometry.setDrawRange(0, n);
    dp.needsUpdate = true;

    // head ring at the nose, turned towards the wearer
    this.head.visible = this.have[0];
    if (this.have[0]) {
      this.head.position.set(this.cur[0], this.cur[1], this.cur[2]);
      if (headLocal) this.head.rotation.y = Math.atan2(headLocal.x - this.cur[0], headLocal.z - this.cur[2]);
      top = Math.max(top, this.cur[1] + HEAD_R);
    }

    const frozen = !!s.frozen;
    const c = frozen ? COLORS.predicted : color;
    const a = frozen ? alpha * (0.25 + 0.3 * (0.5 + 0.5 * Math.sin(performance.now() / 200))) : alpha;
    this.mat.color.setHex(c); this.dotMat.color.setHex(c);
    this.mat.opacity = a; this.dotMat.opacity = a;
    this.mat.depthTest = this.dotMat.depthTest = !hidden;   // behind a wall: draw on top, like the box
    this.bones.renderOrder = this.dots.renderOrder = this.head.renderOrder = hidden ? 11 : 1;
    this.object.visible = true;
    this.top = top;
    return true;
  }

  dispose() {
    this.bones.geometry.dispose(); this.dots.geometry.dispose();
    this.mat.dispose(); this.dotMat.dispose();
  }
}
