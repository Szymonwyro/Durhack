// Static map content: walls (cyan outlines), origin cross (orange), alignment-point crosses, cameras.
import * as THREE from 'three';
import { COLORS, ALIGN_POINTS } from './config.js';
import { mapRoot, mapToThree } from './frames.js';

const group = new THREE.Group();
group.name = 'world';
mapRoot.add(group);

const v = (xy, z = 0) => mapToThree(xy, z);

function lines(points, color, opts = {}) {
  const geom = new THREE.BufferGeometry().setFromPoints(points);
  return new THREE.LineSegments(geom, new THREE.LineBasicMaterial({ color, ...opts }));
}

// flat cross on the floor centred at map xy, arms of half-length r
function crossPoints(xy, r, out) {
  const z = 0.005;
  out.push(v([xy[0] - r, xy[1]], z), v([xy[0] + r, xy[1]], z));
  out.push(v([xy[0], xy[1] - r], z), v([xy[0], xy[1] + r], z));
}

let current = { walls: [], cameras: [], zones: [] };
export const getWorld = () => current;

export function buildWorld(world) {
  current = { walls: world.walls || [], cameras: world.cameras || [], zones: world.zones || [] };
  for (const c of [...group.children]) { group.remove(c); c.geometry.dispose(); c.material.dispose(); }

  // walls: rectangle outline in the wall plane + a mid rail, all in one draw call
  const w = [];
  for (const { a, b, h = 2.0 } of current.walls) {
    w.push(v(a), v(b), v(b), v(b, h), v(b, h), v(a, h), v(a, h), v(a));
    w.push(v(a, h / 2), v(b, h / 2));
  }
  if (w.length) group.add(lines(w, COLORS.wall));

  // origin (corner marker ID 1)
  const o = [];
  crossPoints([0, 0], 0.25, o);
  group.add(lines(o, COLORS.origin));

  // alignment points (skip the origin, already drawn)
  const p = [];
  for (const { pos } of ALIGN_POINTS) if (pos[0] || pos[1]) crossPoints(pos, 0.12, p);
  if (p.length) group.add(lines(p, COLORS.alignPoint));

  // cameras: a short view direction + field-of-view edges at camera height
  const c = [];
  for (const cam of current.cameras) {
    const [x, y, z = 1.5] = cam.pos;
    const yaw = THREE.MathUtils.degToRad(cam.yaw || 0);
    const half = THREE.MathUtils.degToRad((cam.fov || 60) / 2);
    const at = (ang, len) => v([x + len * Math.cos(ang), y + len * Math.sin(ang)], z);
    c.push(v([x, y], z), at(yaw, 0.4));
    c.push(v([x, y], z), at(yaw - half, 0.3), v([x, y], z), at(yaw + half, 0.3));
    c.push(v([x, y], 0), v([x, y], z)); // pole down to the floor
  }
  if (c.length) group.add(lines(c, COLORS.alignPoint, { transparent: true, opacity: 0.6 }));
}
