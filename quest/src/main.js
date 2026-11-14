// Quest 2 WebXR viewer entry point: passthrough AR, calibration by touching corner markers,
// people from /tracks as floor-locked boxes. Live by default; ?mock=1 for fake data; ?debug=1 for stats.
import * as THREE from 'three';
import { MOCK, DEBUG, DATA_TIMEOUT_S, FADE_S } from './config.js';
import { mapRoot, worldToMap } from './frames.js';
import { buildWorld, buildCorners, setLayers, getWorld } from './world.js';
import { buildZones, updateZones } from './zones.js';
import { tracksGroup, onTracksMsg, updateTracks, trackCount } from './tracks.js';
import { attachHud, updateHud } from './hud.js';
import { initAlign, setWorld as alignSetWorld, getPoints, getState, getFit, updateAlign } from './align.js';
import { createFeed, startNet, invalidCounts } from './net.js';
import { startMock } from './mock.js';

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
scene.add(camera); // head-locked panels are children of the camera
scene.add(mapRoot);
attachHud(camera);
initAlign(renderer, scene, camera);
alignSetWorld({ world_version: 0, align_points: [] }); // fallback corners until /world arrives
buildCorners(getPoints());

window.addEventListener('resize', () => {
  if (renderer.xr.isPresenting) return; // XR owns the size while presenting
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// --- data ---
let netStatus = MOCK ? 'mock' : 'connecting';
let pirZones = [];
const feed = createFeed({
  onWorld: (w, info) => {
    buildWorld(w);
    buildZones(w.zones);
    alignSetWorld(w, info);
    buildCorners(getPoints());
    console.log('[world] v' + w.world_version, w.walls.length, 'walls,', w.objects.length, 'objects,',
      w.zones.length, 'zones,', w.align_points.length, 'align points');
  },
  onTracks: (m) => { onTracksMsg(m); pirZones = m.zones; },
});
if (MOCK) startMock(feed);
else startNet(feed, (s) => { netStatus = s; console.log('[net]', s); });

// --- enter AR (own button: three's ARButton forces the 'local' reference space) ---
const sessionInit = { requiredFeatures: ['local-floor'], optionalFeatures: ['hand-tracking', 'anchors'] };
let session = null;

async function checkSupport() {
  if (!window.isSecureContext) return 'Not a secure context. Open via http://localhost:8001 (adb reverse) or https.';
  if (!navigator.xr) return 'WebXR is not available in this browser (use the Quest browser).';
  try {
    if (!(await navigator.xr.isSessionSupported('immersive-ar'))) return 'immersive-ar (passthrough AR) is not supported here.';
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
  const presenting = renderer.xr.isPresenting;

  updateAlign(now, presenting);
  const st = getState(), aligned = st === 'aligned';

  // data freshness: > 1 s without /tracks -> fade over 1 s -> idle
  const age = feed.dataAge();
  const fade = Math.min(1, Math.max(0, 1 - (age - DATA_TIMEOUT_S) / FADE_S));

  setLayers({ corners: aligned || st === 'review', objects: aligned && fade > 0 });
  tracksGroup.visible = aligned;

  camera.getWorldPosition(headWorld);
  const head = presenting ? worldToMap(headWorld) : null;
  updateTracks(dt, head, getWorld().walls, fade, feed.serverNow());
  updateZones(aligned ? pirZones : [], now, fade);

  if (DEBUG) {
    const fit = getFit();
    const nBad = Object.values(invalidCounts).reduce((s, n) => s + n, 0);
    updateHud(now, [
      `${MOCK ? 'mock' : 'live'} · ${netStatus}`.slice(0, 34),
      `align ${st}${fit ? ' ' + Math.round(fit.rms * 100) + 'cm' : ''} · wv ${feed.worldVersion() ?? '-'}`,
      `age ${Number.isFinite(age) ? age.toFixed(2) : '-'}s · tracks ${trackCount()}`,
      `clock ${feed.clockOffset().toFixed(2)}s · bad ${nBad}`,
    ]);
  }
  renderer.render(scene, camera);
});
