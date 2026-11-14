// ?mock=1 : fake /world + /tracks messages, shaped exactly like the contracts in CLAUDE.md, at 15 Hz.
// One person walks an ellipse that crosses the wall at x=1.0. camA is on the x<1 side, so while the
// person is behind that wall (x > 1.1) no camera sees them: state goes 'predicted' (~3 s) and cov grows.

export const MOCK_WORLD = {
  walls: [
    { a: [1.0, 0.0], b: [1.0, 1.5], h: 2.0 },
    { a: [2.0, 2.0], b: [3.0, 2.0], h: 2.0 },
  ],
  cameras: [{ id: 'camA', pos: [-0.5, -0.5, 1.5], yaw: 45.0, fov: 70.0 }],
  zones: [],
};

const C = [1.0, 0.75], RX = 0.8, RY = 0.5, PERIOD = 6.5; // ~3 s of each lap is behind the wall

export function startMock({ onWorld, onTracks }) {
  onWorld(structuredClone(MOCK_WORLD));
  const t0 = Date.now() / 1000;
  let lastSeen = t0;

  const timer = setInterval(() => {
    const t = Date.now() / 1000;
    const w = (2 * Math.PI) / PERIOD;
    const ang = w * (t - t0);
    const pos = [C[0] + RX * Math.cos(ang), C[1] + RY * Math.sin(ang)];
    const vel = [-RX * w * Math.sin(ang), RY * w * Math.cos(ang)];
    const seen = pos[0] <= 1.1;
    if (seen) lastSeen = t;
    const unseen = t - lastSeen;
    const s2 = 0.08 ** 2 + (0.35 * unseen) ** 2; // variance grows while predicted
    onTracks({
      t,
      tracks: [{
        id: 7,
        state: seen ? 'visible' : 'predicted',
        pos, vel,
        cov: [[s2, 0], [0, s2 * 0.6]],
        last_seen: lastSeen,
        seen_by: seen ? ['camA'] : [],
      }],
    });
  }, 1000 / 15);

  return () => clearInterval(timer);
}
