// Line of sight on the floor plane (map coords, metres). Pure 2D, no three.js.

// true if segment p-q crosses segment a-b (touching the far end q doesn't count)
export function segmentsIntersect(p, q, a, b) {
  const rx = q[0] - p[0], ry = q[1] - p[1];
  const sx = b[0] - a[0], sy = b[1] - a[1];
  const den = rx * sy - ry * sx;
  if (Math.abs(den) < 1e-9) return false; // parallel
  const ax = a[0] - p[0], ay = a[1] - p[1];
  const t = (ax * sy - ay * sx) / den; // along p-q
  const u = (ax * ry - ay * rx) / den; // along a-b
  return t > 0 && t < 1 && u >= 0 && u <= 1;
}

// index of the first wall between head and target, or -1 if line of sight is clear
export function blockingWall(head, target, walls) {
  for (let i = 0; i < walls.length; i++) {
    if (segmentsIntersect(head, target, walls[i].a, walls[i].b)) return i;
  }
  return -1;
}
