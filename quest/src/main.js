// Quest 2 WebXR viewer entry point.
import * as THREE from 'three';
import { MOCK } from './config.js';
import { mapRoot, worldToMap } from './frames.js';
import { buildWorld, getWorld } from './world.js';
import { onTracksMsg, updateTracks, trackStats } from './tracks.js';
import { attachHud, updateHud } from './hud.js';
import { initAlign, loadAlignment, updateAlign, alignStatus } from './align.js';
import { startMock } from './mock.js';
import { startLive } from './source.js';

const enterBtn = document.getElementById('enter');
const statusEl = document.getElementById('status');
const setStatus = (msg) => { statusEl.textContent = msg; };

// --- renderer / scene ---
const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setClearColor(0x000000, 0); // transparent => passthrough shows
renderer.xr.enabled = true;
renderer.xr.setReferenceSpaceType('local-floor');
renderer.xr.setFoveation(1);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.05, 50);
scene.add(camera); // so the head-locked HUD (a child of camera) renders
scene.add(mapRoot);
attachHud(camera);
initAlign(renderer, scene);
loadAlignment();

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// --- data source ---
let srcStatus = '';
const handlers = {
  onWorld: (w) => { buildWorld(w); console.log('[world]', w.walls.length, 'walls', (w.cameras || []).length, 'cameras'); },
  onTracks: onTracksMsg,
  onStatus: (s) => { srcStatus = s; console.log('[source]', s); },
};
if (MOCK) startMock(handlers); else startLive(handlers);

// --- enter AR (own button: three's ARButton forces the 'local' reference space) ---
const sessionInit = { requiredFeatures: ['local-floor'], optionalFeatures: ['hand-tracking', 'anchors'] };
let session = null;

async function checkSupport() {
  if (!window.isSecureContext) return 'Not a secure context. Open via http://localhost:8001 (adb reverse) or https.';
  if (!navigator.xr) return 'WebXR is not available in this browser (use the Quest browser).';
  try {
    if (!(await navigator.xr.isSessionSupported('immersive-ar'))) {
      return 'immersive-ar (passthrough AR) is not supported on this device/browser.';
    }
  } catch (e) {
    return 'isSessionSupported failed: ' + e;
  }
  return null;
}

async function enterAR() {
  if (session) { session.end(); return; }
  try {
    session = await navigator.xr.requestSession('immersive-ar', sessionInit);
  } catch (e) {
    setStatus('Could not start AR: ' + e.message);
    return;
  }
  session.addEventListener('end', () => { session = null; enterBtn.textContent = 'Enter AR'; });
  await renderer.xr.setSession(session);
  enterBtn.textContent = 'Exit AR';
}

checkSupport().then((problem) => {
  if (problem) { enterBtn.textContent = 'AR not available'; setStatus(problem); return; }
  enterBtn.disabled = false;
  enterBtn.textContent = 'Enter AR';
  enterBtn.addEventListener('click', enterAR);
  setStatus(`mode: ${MOCK ? 'mock' : 'live'}`);
});

// --- render loop ---
const headWorld = new THREE.Vector3();
let last = performance.now() / 1000;
renderer.setAnimationLoop(() => {
  const now = performance.now() / 1000;
  const dt = Math.min(now - last, 0.1);
  last = now;

  updateAlign();
  camera.getWorldPosition(headWorld);
  const head = renderer.xr.isPresenting ? worldToMap(headWorld) : null;
  updateTracks(dt, head, getWorld().walls);

  const al = alignStatus();
  const ts = trackStats();
  updateHud(now, {
    mode: MOCK ? 'mock' : 'live', align: al.text, prompt: al.prompt, warn: al.warn,
    age: ts.age, count: ts.count, status: srcStatus,
  });
  renderer.render(scene, camera);
});
