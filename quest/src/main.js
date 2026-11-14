// Quest 2 WebXR viewer entry point.
import * as THREE from 'three';
import { MOCK, DEBUG } from './config.js';
import { mapRoot, mapToThree } from './frames.js';

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
scene.add(mapRoot);

// Step 1 test object: 0.3 m wireframe cube on the floor 1 m in front of the start pose (map +y = forward).
const testCube = new THREE.LineSegments(
  new THREE.EdgesGeometry(new THREE.BoxGeometry(0.3, 0.3, 0.3)),
  new THREE.LineBasicMaterial({ color: 0x00e5ff }),
);
mapToThree([0, 1.0], 0.15, testCube.position);
mapRoot.add(testCube);

window.addEventListener('resize', () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});

// --- enter AR (own button: three's ARButton forces the 'local' reference space) ---
const sessionInit = { requiredFeatures: ['local-floor'], optionalFeatures: ['hand-tracking', 'anchors'] };
let session = null;

async function checkSupport() {
  if (!window.isSecureContext) {
    return 'Not a secure context. Open via http://localhost:8001 (adb reverse) or https.';
  }
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
  setStatus('');
}

checkSupport().then((problem) => {
  if (problem) {
    enterBtn.textContent = 'AR not available';
    setStatus(problem);
    return;
  }
  enterBtn.disabled = false;
  enterBtn.textContent = 'Enter AR';
  enterBtn.addEventListener('click', enterAR);
  setStatus(`mode: ${MOCK ? 'mock' : 'live'}${DEBUG ? ' · debug' : ''}`);
});

// --- render loop ---
renderer.setAnimationLoop((time, frame) => {
  testCube.rotation.y = time / 1000;
  renderer.render(scene, camera);
});
