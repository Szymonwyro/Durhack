// Live data per quest/INTERFACE.md: GET /world, then WS /tracks (world message first, then tracks ~15 Hz).
// Every message is validated; invalid ones are dropped with one console warning per kind.
// The mock (?mock=1) feeds the same createFeed() so it exercises the same validation.
import { loadServerUrl, wsUrl } from '/config/server_url.js';
import { RECONNECT_S } from './config.js';

// ---------------------------------------------------------------- validation
export const invalidCounts = {};
const warned = new Set();
function bad(kind, detail) {
  invalidCounts[kind] = (invalidCounts[kind] || 0) + 1;
  if (!warned.has(kind)) { warned.add(kind); console.warn(`[net] dropping invalid ${kind}:`, JSON.stringify(detail)?.slice(0, 200)); }
  return null;
}
const fin = Number.isFinite;
const isVec = (a, n) => Array.isArray(a) && a.length === n && a.every(fin);
const isMat2 = (m) => Array.isArray(m) && m.length === 2 && isVec(m[0], 2) && isVec(m[1], 2);
const isStr = (s) => typeof s === 'string';
function keep(arr, ok, kind) {
  if (arr === undefined) return [];
  if (!Array.isArray(arr)) { bad(kind + '[]', arr); return []; }
  return arr.filter((x) => (x && ok(x)) || bad(kind, x));
}

export function validateWorld(w) {
  if (!w || typeof w !== 'object') return bad('world', w);
  if (w.type !== undefined && w.type !== 'world') return bad('world.type', w.type);
  if (w.v !== 1) return bad('world.v', w.v);
  if (!Number.isInteger(w.world_version)) return bad('world.world_version', w.world_version);
  return {
    v: 1,
    world_version: w.world_version,
    walls: keep(w.walls, (x) => isVec(x.a, 2) && isVec(x.b, 2) && (x.h === undefined || fin(x.h)), 'world.wall')
      .map((x) => ({ a: x.a, b: x.b, h: x.h ?? 2.0 })),
    cameras: keep(w.cameras, (x) => isStr(x.id) && isVec(x.pos, 3), 'world.camera'),
    zones: keep(w.zones, (x) => isStr(x.sensor) && Array.isArray(x.zone) && x.zone.length >= 3 && x.zone.every((p) => isVec(p, 2)), 'world.zone'),
    objects: keep(w.objects, (x) => isStr(x.id) && isVec(x.pos, 2) && isVec(x.size, 3), 'world.object'),
    align_points: keep(w.align_points, (x) => isVec(x.pos, 2), 'world.align_point'),
  };
}

const STATES = new Set(['visible', 'predicted']);
export function validateTracks(m) {
  if (m.v !== 1) return bad('tracks.v', m.v);
  if (!fin(m.t)) return bad('tracks.t', m.t);
  if (!Array.isArray(m.tracks)) return bad('tracks.tracks', m.tracks);
  return {
    t: m.t,
    world_version: Number.isInteger(m.world_version) ? m.world_version : null,
    tracks: keep(m.tracks, (x) => Number.isInteger(x.id) && STATES.has(x.state) && isVec(x.pos, 2) && isVec(x.vel, 2)
      && isMat2(x.cov) && fin(x.last_seen) && Array.isArray(x.seen_by), 'track'),
    zones: keep(m.zones, (x) => isStr(x.sensor) && typeof x.active === 'boolean', 'tracks.zone'),
  };
}

// ---------------------------------------------------------------- feed (shared by live + mock)
// handlers: onWorld(world, {alignChanged}), onTracks(msg)
export function createFeed(handlers) {
  let worldVersion = null, apKey = null, lastData = -Infinity;
  const offsets = []; // local_unix - server_t; the minimum ~ pure clock offset (least network delay)
  const feed = {
    onWorldVersion: null, // set by startNet: re-fetch /world

    handleWorld(raw) {
      const w = validateWorld(raw);
      if (!w) return;
      const key = JSON.stringify(w.align_points.map((p) => p.pos));
      const alignChanged = apKey !== null && key !== apKey;
      apKey = key;
      worldVersion = w.world_version;
      handlers.onWorld(w, { alignChanged });
    },

    handleText(text) {
      let m;
      try { m = JSON.parse(text); } catch { return bad('json', text); }
      if (!m || typeof m !== 'object') return bad('message', m);
      if (m.type === 'world') return feed.handleWorld(m);
      if (m.type !== 'tracks') return bad('message.type', m.type);
      const tr = validateTracks(m);
      if (!tr) return;
      offsets.push(Date.now() / 1000 - tr.t);
      if (offsets.length > 50) offsets.shift();
      lastData = performance.now() / 1000;
      if (tr.world_version !== null && worldVersion !== null && tr.world_version !== worldVersion && feed.onWorldVersion) {
        feed.onWorldVersion(tr.world_version);
      }
      handlers.onTracks(tr);
    },

    dataAge: () => performance.now() / 1000 - lastData,
    serverNow: () => Date.now() / 1000 - (offsets.length ? Math.min(...offsets) : 0),
    clockOffset: () => (offsets.length ? -Math.min(...offsets) : 0),
    worldVersion: () => worldVersion,
  };
  return feed;
}

// ---------------------------------------------------------------- live connection
// Every URL comes from /config/server_url.js (?server=, ?mode=, or config/server.json) if it yields one,
// else this page's origin (adb reverse, or the cloudflared tunnel). An https page never switches to an
// http server (the browser would block it as mixed content). ws:// or wss:// follows the base's scheme.
let resolvedBase = null;
export const getBase = () => resolvedBase;
export const wsState = { value: 'idle' };

export async function serverBase() {
  if (resolvedBase) return resolvedBase;
  let base = location.origin;
  try {
    const u = await loadServerUrl();
    if (location.protocol === 'https:' && u.startsWith('http:')) {
      console.warn('[net] ignoring configured', u, '- http server from an https page; using', location.origin);
    } else {
      base = u;
    }
  } catch (e) {
    // no usable server.json entry: same origin
  }
  resolvedBase = base;
  return base;
}

export function startNet(feed, onStatus) {
  let base = null, lastRefetch = 0;

  async function fetchWorld() {
    try {
      const res = await fetch(base + '/world', { cache: 'no-store' });
      if (res.ok) feed.handleWorld(await res.json());
      else onStatus(`GET /world ${res.status}`);
    } catch (e) {
      onStatus('GET /world failed');
    }
  }
  feed.onWorldVersion = (v) => {
    const now = performance.now();
    if (now - lastRefetch < 2000) return; // don't hammer /world if versions disagree
    lastRefetch = now;
    console.log('[net] world_version changed to', v, '- re-fetching /world');
    fetchWorld();
  };

  async function connect() {
    base = base || await serverBase();
    await fetchWorld();
    const ws = new WebSocket(wsUrl(base, '/tracks'));
    wsState.value = 'connecting';
    ws.onopen = () => { wsState.value = 'open'; onStatus('connected ' + base); };
    ws.onmessage = (ev) => feed.handleText(ev.data);
    ws.onclose = () => { wsState.value = 'closed'; onStatus('reconnecting'); setTimeout(connect, RECONNECT_S * 1000); };
    ws.onerror = () => {}; // onclose follows
  }
  connect();
}
