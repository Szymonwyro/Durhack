// ?debug=1 only: a small head-locked stats panel (upper-left of the lower field of view).
import * as THREE from 'three';
import { DEBUG } from './config.js';
import { CanvasText } from './label.js';

const text = new CanvasText(512, 230);
const panel = new THREE.Mesh(new THREE.PlaneGeometry(0.17, 0.076),
  new THREE.MeshBasicMaterial({ map: text.texture, transparent: true, depthTest: false }));
panel.position.set(-0.1, -0.06, -0.6); // just left of centre, above the calibration panel
panel.renderOrder = 30;
panel.visible = DEBUG;

export function attachHud(camera) { if (DEBUG) camera.add(panel); }

let lastDraw = 0, frames = 0, fps = 0, fpsT = 0;

export function updateHud(now, lines) {
  frames++;
  if (now - fpsT >= 1) { fps = frames / (now - fpsT); frames = 0; fpsT = now; }
  if (!DEBUG || now - lastDraw < 0.25) return;
  lastDraw = now;
  text.draw([`fps ${fps.toFixed(0)}`, ...lines], { color: '#00E5FF' });
}
