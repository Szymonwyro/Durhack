// ?debug=1 remote debugging (works untethered, e.g. through the tunnel):
// console errors/warnings + uncaught errors, and a 1 Hz status line, are POSTed to <server>/log as
//   {"client":"quest","entries":[{"t":<unix>,"level":"error|warn|status","msg":"..."}]}
// If the server has no /log (404 / network error) posting pauses for 30 s; the last 5 errors are always
// kept for the in-headset debug panel.
import { DEBUG } from './config.js';
import { serverBase } from './net.js';

const recent = [];   // last 5 errors/warnings, newest last
let queue = [];
let pausedUntil = 0;
export let logEndpoint = DEBUG ? 'pending' : 'off'; // pending | ok | missing | off

function push(level, args) {
  const msg = args.map((a) => (a instanceof Error ? a.message : typeof a === 'string' ? a : safeJson(a))).join(' ').slice(0, 300);
  recent.push(`${level === 'error' ? 'E' : 'W'} ${msg}`);
  if (recent.length > 5) recent.shift();
  queue.push({ t: Date.now() / 1000, level, msg });
}
function safeJson(a) { try { return JSON.stringify(a); } catch { return String(a); } }

export const recentErrors = () => recent;

export function initDebugLog(getStatus) {
  if (!DEBUG) return;
  for (const level of ['error', 'warn']) {
    const orig = console[level].bind(console);
    console[level] = (...args) => { push(level, args); orig(...args); };
  }
  window.addEventListener('error', (e) => push('error', [`${e.message} @ ${e.filename?.split('/').pop()}:${e.lineno}`]));
  window.addEventListener('unhandledrejection', (e) => push('error', ['unhandled rejection:', e.reason]));

  setInterval(async () => {
    queue.push({ t: Date.now() / 1000, level: 'status', msg: getStatus() });
    if (queue.length > 200) queue = queue.slice(-200);
    if (performance.now() < pausedUntil) return;
    const entries = queue;
    queue = [];
    try {
      const base = await serverBase();
      const res = await fetch(base + '/log', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ client: 'quest', entries }),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      logEndpoint = 'ok';
    } catch (e) {
      if (logEndpoint !== 'missing') recent.push('W POST /log unavailable - errors shown here only');
      if (recent.length > 5) recent.shift();
      logEndpoint = 'missing';
      pausedUntil = performance.now() + 30000;
    }
  }, 1000);
}
