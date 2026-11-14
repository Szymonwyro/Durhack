// Map content from /world: calibration corners (cyan crosses + IDs), objects (labelled outlines),
// and, only with ?debug=1, walls and cameras. All under mapRoot.
import * as THREE from 'three';
import { COLORS, DEBUG } from './config.js';
import { mapRoot, mapToThree } from './frames.js';
import { textSprite, css } from './label.js';

const debugGroup = new THREE.Group();
const cornerGroup = new THREE.Group();
const objectGroup = new THREE.Group();
mapRoot.add(debugGroup, cornerGroup, objectGroup);

let current = { walls: [], cameras: [], zones: [], objects: [], align_points: [] };
export const getWorld = () => current;

const v = (xy, z = 0) => mapToThree(xy, z);

function clear(g) {
  for (const c of [...g.children]) {
    g.remove(c);
    c.geometry?.dispose();
    if (c.material) { c.material.map?.dispose(); c.material.dispose(); }
  }
}

function lines(points, color, opts = {}) {
  return new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color, ...opts }));
}

export function buildWorld(world) {
  current = world;
  clear(debugGroup);
  clear(objectGroup);

  // debug: walls (outline + mid rail) and cameras
  const w = [];
  for (const { a, b, h } of world.walls) {
    w.push(v(a), v(b), v(b), v(b, h), v(b, h), v(a, h), v(a, h), v(a), v(a, h / 2), v(b, h / 2));
  }
  if (w.length) debugGroup.add(lines(w, COLORS.wall, { transparent: true, opacity: 0.5 }));
  const c = [];
  for (const cam of world.cameras) {
    const [x, y, z] = cam.pos;
    const yaw = THREE.MathUtils.degToRad(cam.yaw || 0), half = THREE.MathUtils.degToRad((cam.fov || 60) / 2);
    const at = (ang, len) => v([x + len * Math.cos(ang), y + len * Math.sin(ang)], z);
    c.push(v([x, y], z), at(yaw, 0.4), v([x, y], z), at(yaw - half, 0.3), v([x, y], z), at(yaw + half, 0.3), v([x, y], 0), v([x, y], z));
  }
  if (c.length) debugGroup.add(lines(c, 0xffffff, { transparent: true, opacity: 0.5 }));

  // objects: box outline (w along map x, d along map y, h up) + label
  for (const o of world.objects) {
    const [bw, bd, bh] = o.size;
    const color = COLORS.object[o.type] ?? COLORS.object.other;
    const geom = new THREE.EdgesGeometry(new THREE.BoxGeometry(bw, bh, bd));
    geom.translate(0, bh / 2, 0);
    const box = new THREE.LineSegments(geom, new THREE.LineBasicMaterial({ color }));
    mapToThree(o.pos, 0, box.position);
    objectGroup.add(box);
    const { text, sprite } = textSprite(384, 80, 0.5);
    text.draw([o.label || o.type], { color: css(color), center: true });
    mapToThree(o.pos, bh + 0.15, sprite.position);
    objectGroup.add(sprite);
  }
}

// calibration corners: small cyan floor crosses with the marker ID
export function buildCorners(points) {
  clear(cornerGroup);
  const p = [], r = 0.1, z = 0.004;
  for (const { pos } of points) {
    p.push(v([pos[0] - r, pos[1]], z), v([pos[0] + r, pos[1]], z), v([pos[0], pos[1] - r], z), v([pos[0], pos[1] + r], z));
  }
  cornerGroup.add(lines(p, COLORS.corner));
  points.forEach((ap, i) => {
    const { text, sprite } = textSprite(128, 96, 0.12);
    text.draw([String(ap.marker ?? i)], { color: css(COLORS.corner), center: true });
    mapToThree([ap.pos[0] + 0.12, ap.pos[1] + 0.12], 0.08, sprite.position);
    cornerGroup.add(sprite);
  });
}

export function setLayers({ corners, objects }) {
  cornerGroup.visible = corners;
  objectGroup.visible = objects;
  debugGroup.visible = DEBUG && corners; // only meaningful once aligned
}
