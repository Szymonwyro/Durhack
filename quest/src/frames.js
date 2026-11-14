// The ONLY place map <-> three.js coordinates are converted.
// Map frame: metres, x,y on the floor, z up (corner marker ID 1 = origin).
// three.js:  y up, so map (x, y, z) -> three (x, z, -y).
// All map content lives under mapRoot; alignment sets mapRoot's yaw (about Y) and floor offset.
import * as THREE from 'three';

export const mapRoot = new THREE.Group();
mapRoot.name = 'mapRoot';

// map [x, y] (+ optional height z) -> THREE.Vector3 in mapRoot-local coords
export function mapToThree(xy, z = 0, out = new THREE.Vector3()) {
  return out.set(xy[0], z, -xy[1]);
}

// THREE.Vector3 in mapRoot-local coords -> map [x, y]
export function threeToMap(v) {
  return [v.x, -v.z];
}

// world-space Vector3 (e.g. the head) -> map [x, y], via mapRoot's current alignment
const _tmp = new THREE.Vector3();
export function worldToMap(vWorld) {
  mapRoot.updateMatrixWorld();
  return threeToMap(mapRoot.worldToLocal(_tmp.copy(vWorld)));
}
