// ?mock=1 : fake server messages exactly per quest/INTERFACE.md, fed through the same validation as live.
// Cycle (28 s): a person walks 2 laps (9 s each) of an ellipse that
//   - passes behind wall A (x > 2.1): camB still sees them -> 'visible', the wearer decides 'behind wall'
//   - crosses a blind spot (x < 1.0, y > 0.3): no camera -> 'predicted' (~2.5 s), PIR pir1 active
// then 10 s of empty tracks (heartbeat only) to exercise the idle state. Each cycle gets a new id.
import { FALLBACK_ALIGN_POINTS } from './config.js';

const BLIND = [[-0.3, 0.3], [1.0, 0.3], [1.0, 2.0], [-0.3, 2.0]];
const inBlind = ([x, y]) => x >= -0.3 && x <= 1.0 && y >= 0.3 && y <= 2.0;

export const MOCK_WORLD = {
  v: 1,
  world_version: 1,
  walls: [
    { a: [2.1, -0.6], b: [2.1, 2.0], h: 2.0 },
    { a: [-0.4, 2.1], b: [1.2, 2.1], h: 2.0 },
  ],
  cameras: [
    { id: 'camA', pos: [-0.6, -0.6, 1.5], yaw: 45, fov: 70 },
    { id: 'camB', pos: [2.8, 0.8, 1.5], yaw: 180, fov: 70 },
  ],
  zones: [{ sensor: 'pir1', zone: BLIND }],
  objects: [{ id: 'exit1', type: 'exit', pos: [-0.6, 0.9], size: [0.1, 0.9, 2.0], label: 'Fire exit' }],
  align_points: FALLBACK_ALIGN_POINTS,
};

const C = [1.4, 0.8], RX = 1.3, RY = 1.0, LAP = 9, WALK = 2 * LAP, GAP = 10, CYCLE = WALK + GAP;
const TAU = 1.2; // prediction: velocity decays with this time constant

export function startMock(feed) {
  feed.handleWorld(structuredClone(MOCK_WORLD));                                  // = GET /world
  feed.handleText(JSON.stringify({ type: 'world', ...MOCK_WORLD }));              // = first WS message
  const t0 = Date.now() / 1000;
  let seen = null; // last camera observation {t, pos, vel, cams}

  setInterval(() => {
    const t = Date.now() / 1000;
    const el = t - t0, cycle = Math.floor(el / CYCLE), ph = el - cycle * CYCLE;
    const tracks = [];
    let pir = false;
    if (ph < WALK) {
      const w = (2 * Math.PI) / LAP, a = w * ph;
      const pos = [C[0] + RX * Math.cos(a), C[1] + RY * Math.sin(a)];
      const vel = [-RX * w * Math.sin(a), RY * w * Math.cos(a)];
      const id = 7 + cycle;
      if (!inBlind(pos)) {
        seen = { t, pos, vel, cams: pos[0] > 2.1 ? ['camB'] : ['camA'] };
        tracks.push({ id, state: 'visible', pos, vel, cov: [[0.0025, 0], [0, 0.0025]], last_seen: t, seen_by: seen.cams });
      } else {
        pir = true;
        if (!seen) seen = { t, pos, vel, cams: ['camA'] };
        const dt = t - seen.t, k = TAU * (1 - Math.exp(-dt / TAU)), decay = Math.exp(-dt / TAU);
        const s2 = 0.05 ** 2 + (0.4 * dt) ** 2; // grows while unseen
        const predicted = dt > 0.5;
        tracks.push({
          id, state: predicted ? 'predicted' : 'visible',
          pos: [seen.pos[0] + seen.vel[0] * k, seen.pos[1] + seen.vel[1] * k],
          vel: [seen.vel[0] * decay, seen.vel[1] * decay],
          cov: [[s2, 0], [0, s2]], last_seen: seen.t, seen_by: predicted ? [] : seen.cams,
        });
      }
    } else {
      seen = null;
    }
    feed.handleText(JSON.stringify({
      type: 'tracks', v: 1, t, world_version: MOCK_WORLD.world_version, tracks,
      zones: [{ sensor: 'pir1', active: pir, t }],
    }));
  }, 1000 / 15);
}
