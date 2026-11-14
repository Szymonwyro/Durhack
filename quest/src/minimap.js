// Small game-style radar, lower-right of view, heading-up: what's around you, top-down.
// You = white arrow in the centre, the tracked area (calibration corners) = cyan dashed outline,
// walls = white lines, objects = small coloured boxes, active PIR zones = orange, people = dots
// (cyan in view, orange behind a wall, dim amber predicted). Redrawn at ~12 Hz. B toggles it.
import * as THREE from 'three';
import { COLORS } from './config.js';
import { css } from './label.js';

const SIZE = 320;          // canvas px
const RANGE_M = 2.5;       // radius shown (m)
const R = SIZE / 2 - 6;    // drawable radius px
const S = R / RANGE_M;     // px per metre

export const canvas = document.createElement('canvas'); // exported for tests
canvas.width = canvas.height = SIZE;
const ctx = canvas.getContext('2d');
const texture = new THREE.CanvasTexture(canvas);
texture.colorSpace = THREE.SRGBColorSpace;
const mesh = new THREE.Mesh(new THREE.CircleGeometry(0.055, 48),
  new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthTest: false }));
// CircleGeometry UVs map the canvas square onto the disc
mesh.position.set(0.15, -0.13, -0.5);
mesh.rotation.x = 0.35;
mesh.renderOrder = 35;
mesh.visible = false;

let enabled = true, lastDraw = 0;
export function attachMinimap(camera) { camera.add(mesh); }
export function toggleMinimap() { enabled = !enabled; }

// head: [x,y] map; heading: map angle (rad, CCW from +x) the wearer faces
// data: { corners: [[x,y]], walls, objects, zones (world polygons), activeZones: Set, dots: [{pos,color,alpha}] }
export function updateMinimap(now, show, head, heading, data) {
  mesh.visible = show && enabled && !!head;
  if (!mesh.visible || now - lastDraw < 0.08) return;
  lastDraw = now;

  // map point -> canvas: relative to head, rotated so the facing direction points up
  const rot = Math.PI / 2 - heading, c = Math.cos(rot), s = Math.sin(rot);
  const P = ([x, y]) => {
    const dx = x - head[0], dy = y - head[1];
    return [SIZE / 2 + (c * dx - s * dy) * S, SIZE / 2 - (s * dx + c * dy) * S];
  };

  ctx.clearRect(0, 0, SIZE, SIZE);
  ctx.save();
  ctx.beginPath();
  ctx.arc(SIZE / 2, SIZE / 2, R, 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(0,0,0,0.62)';
  ctx.fill();
  ctx.clip();

  // range rings every metre + forward field-of-view wedge
  ctx.strokeStyle = 'rgba(255,255,255,0.12)';
  ctx.lineWidth = 1.5;
  for (let m = 1; m < RANGE_M; m++) { ctx.beginPath(); ctx.arc(SIZE / 2, SIZE / 2, m * S, 0, Math.PI * 2); ctx.stroke(); }
  ctx.fillStyle = 'rgba(0,229,255,0.10)';
  ctx.beginPath();
  ctx.moveTo(SIZE / 2, SIZE / 2);
  ctx.arc(SIZE / 2, SIZE / 2, R, -Math.PI / 2 - 0.55, -Math.PI / 2 + 0.55);
  ctx.closePath();
  ctx.fill();

  const poly = (pts, close) => {
    ctx.beginPath();
    pts.map(P).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    if (close) ctx.closePath();
  };

  // active PIR zones
  for (const z of data.zones) {
    if (!data.activeZones.has(z.sensor)) continue;
    poly(z.zone, true);
    ctx.fillStyle = 'rgba(255,138,0,' + (0.25 + 0.2 * Math.sin(now * 6)) + ')';
    ctx.fill();
  }
  // tracked area boundary
  if (data.corners.length > 2) {
    poly(data.corners, true);
    ctx.setLineDash([8, 6]);
    ctx.strokeStyle = css(COLORS.corner);
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.setLineDash([]);
  }
  // walls
  ctx.strokeStyle = '#FFFFFF';
  ctx.lineWidth = 5;
  ctx.lineCap = 'round';
  for (const w of data.walls) { poly([w.a, w.b], false); ctx.stroke(); }
  // objects: footprint rectangles
  ctx.lineWidth = 3;
  for (const o of data.objects) {
    const [x, y] = o.pos, hw = o.size[0] / 2, hd = o.size[1] / 2;
    poly([[x - hw, y - hd], [x + hw, y - hd], [x + hw, y + hd], [x - hw, y + hd]], true);
    ctx.strokeStyle = css(COLORS.object[o.type] ?? COLORS.object.other);
    ctx.stroke();
  }
  // people
  for (const d of data.dots) {
    const [x, y] = P(d.pos);
    ctx.globalAlpha = d.predicted ? 0.45 + 0.3 * Math.sin(now * 5) : d.alpha;
    ctx.beginPath();
    ctx.arc(x, y, d.predicted ? 9 : 11, 0, Math.PI * 2);
    ctx.fillStyle = css(d.color);
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  ctx.restore();

  // you: arrow pointing up (= where you face)
  ctx.fillStyle = '#FFFFFF';
  ctx.beginPath();
  ctx.moveTo(SIZE / 2, SIZE / 2 - 14);
  ctx.lineTo(SIZE / 2 + 9, SIZE / 2 + 10);
  ctx.lineTo(SIZE / 2, SIZE / 2 + 5);
  ctx.lineTo(SIZE / 2 - 9, SIZE / 2 + 10);
  ctx.closePath();
  ctx.fill();
  // rim
  ctx.beginPath();
  ctx.arc(SIZE / 2, SIZE / 2, R, 0, Math.PI * 2);
  ctx.strokeStyle = css(COLORS.corner);
  ctx.lineWidth = 4;
  ctx.stroke();
  texture.needsUpdate = true;
}
