// ?debug=1 only: a small head-locked stats panel (just left of centre) + the last 5 errors/warnings.
import * as THREE from 'three';
import { DEBUG } from './config.js';
import { CanvasText } from './label.js';

const text = new CanvasText(640, 420);
const panel = new THREE.Mesh(new THREE.PlaneGeometry(0.2, 0.131),
  new THREE.MeshBasicMaterial({ map: text.texture, transparent: true, depthTest: false }));
panel.position.set(-0.11, -0.02, -0.6); // just left of centre, above the calibration panel
panel.renderOrder = 30;
panel.visible = DEBUG;

export function attachHud(camera) { if (DEBUG) camera.add(panel); }

let lastDraw = 0, frames = 0, fps = 0, fpsT = 0;
export const getFps = () => fps;

// lines: status lines; errors: last few errors (drawn in orange-ish via a leading "!")
export function updateHud(now, lines, errors = []) {
  frames++;
  if (now - fpsT >= 1) { fps = frames / (now - fpsT); frames = 0; fpsT = now; }
  if (!DEBUG || now - lastDraw < 0.25) return;
  lastDraw = now;
  const all = [`fps ${fps.toFixed(0)}`, ...lines];
  for (let i = 0; i < 5; i++) all.push(errors[i] ? errors[i].slice(0, 44) : '');
  text.draw(all, { color: errors.length ? '#FF8A00' : '#00E5FF' });
}
