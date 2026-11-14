// Live source: GET /world, then WebSocket /tracks (server may also push a world message on connect).
// Server URL: ?server=<url>, or ?mode=usb|lan|tunnel via config/server.json, else this page's origin
// (the page is normally served by the same server, e.g. http://localhost:8001 over adb reverse).
import { loadServerUrl, wsUrl } from '/config/server_url.js';

async function serverBase() {
  const q = new URLSearchParams(location.search);
  if (q.get('server') || q.get('mode')) return loadServerUrl();
  return location.origin;
}

export function startLive({ onWorld, onTracks, onStatus }) {
  let ws = null, stopped = false, retry = null;

  async function connect() {
    let base;
    try {
      base = await serverBase();
      const res = await fetch(base + '/world', { cache: 'no-store' });
      if (res.ok) onWorld(await res.json());
    } catch (e) {
      onStatus(`world: ${e.message || e}`);
    }
    if (!base) { retry = setTimeout(connect, 2000); return; }

    ws = new WebSocket(wsUrl(base, '/tracks'));
    ws.onopen = () => onStatus('connected ' + base);
    ws.onmessage = (ev) => {
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.tracks) onTracks(msg);
      else if (msg.walls) onWorld(msg);
    };
    ws.onclose = () => {
      onStatus('disconnected, retrying');
      if (!stopped) retry = setTimeout(connect, 1000);
    };
    ws.onerror = () => {}; // onclose follows
  }

  connect();
  return () => { stopped = true; clearTimeout(retry); if (ws) ws.close(); };
}
