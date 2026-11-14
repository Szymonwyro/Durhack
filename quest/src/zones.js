// PIR zones: floor polygons from world.zones; an active zone (tracks.zones) pulses orange.
import * as THREE from 'three';
import { COLORS } from './config.js';
import { mapRoot, mapToThree } from './frames.js';

const group = new THREE.Group();
mapRoot.add(group);
const zones = new Map(); // sensor -> { fill, line }

export function buildZones(worldZones) {
  for (const z of zones.values()) {
    group.remove(z.fill, z.line);
    z.fill.geometry.dispose(); z.fill.material.dispose(); z.line.geometry.dispose(); z.line.material.dispose();
  }
  zones.clear();
  for (const { sensor, zone } of worldZones) {
    const shape = new THREE.Shape(zone.map(([x, y]) => new THREE.Vector2(x, y)));
    const fillGeom = new THREE.ShapeGeometry(shape);
    fillGeom.rotateX(-Math.PI / 2); // shape (x, y, 0) -> three (x, 0, -y) = map frame on the floor
    fillGeom.translate(0, 0.006, 0);
    const fill = new THREE.Mesh(fillGeom, new THREE.MeshBasicMaterial({
      color: COLORS.zone, transparent: true, opacity: 0.2, depthWrite: false, side: THREE.DoubleSide,
    }));
    const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(zone.map((p) => mapToThree(p, 0.01))),
      new THREE.LineBasicMaterial({ color: COLORS.zone, transparent: true }));
    fill.visible = line.visible = false;
    group.add(fill, line);
    zones.set(sensor, { fill, line });
  }
}

// active: array of {sensor, active}; fade 0..1
export function updateZones(active, now, fade) {
  const on = new Set(active.filter((z) => z.active).map((z) => z.sensor));
  const pulse = 0.5 + 0.5 * Math.sin(now * 6);
  for (const [sensor, z] of zones) {
    const vis = on.has(sensor) && fade > 0;
    z.fill.visible = z.line.visible = vis;
    if (vis) {
      z.fill.material.opacity = (0.08 + 0.22 * pulse) * fade;
      z.line.material.opacity = (0.4 + 0.6 * pulse) * fade;
    }
  }
}
