// Head-locked canvas panel. In ?debug=1 it always shows stats; otherwise only prompts/warnings.
import * as THREE from 'three';
import { DEBUG } from './config.js';
import { CanvasText } from './label.js';

const text = new CanvasText(512, 256);
const panel = new THREE.Mesh(
  new THREE.PlaneGeometry(0.24, 0.12),
  new THREE.MeshBasicMaterial({ map: text.texture, transparent: true, depthTest: false }),
);
panel.position.set(-0.1, -0.12, -0.55); // lower-left of view
panel.rotation.x = 0.2;
panel.renderOrder = 30;

export function attachHud(camera) { camera.add(panel); }

let lastDraw = 0, frames = 0, fps = 0, fpsT = 0;

// call every frame; fields: { mode, align, prompt, warn, age, count, status }
export function updateHud(now, f) {
  frames++;
  if (now - fpsT >= 1) { fps = frames / (now - fpsT); frames = 0; fpsT = now; }
  if (now - lastDraw < 0.25) return;
  lastDraw = now;

  const lines = [];
  if (f.warn) lines.push(f.warn);
  if (f.prompt) lines.push(f.prompt);
  if (DEBUG) {
    lines.push(`fps ${fps.toFixed(0)}   mode ${f.mode}`);
    lines.push(`align: ${f.align}`);
    lines.push(`msg age ${Number.isFinite(f.age) ? f.age.toFixed(2) + ' s' : '-'}   tracks ${f.count}`);
    if (f.status) lines.push(f.status);
  }
  panel.visible = lines.length > 0;
  if (panel.visible) text.draw(lines, f.warn ? '#FF8C00' : '#00E5FF');
}
