// Pick the fusion-server URL in the browser (map, Quest).
// Order: ?server=<url> query param (browser stand-in for SERVER_URL)  >  server.json "<mode>_url".
//
//   import { loadServerUrl, wsUrl } from "/config/server_url.js";
//   const base = await loadServerUrl();          // e.g. https://172.20.10.3:8000
//   const ws = new WebSocket(wsUrl(base, "/tracks"));
//
// Per-device mode without editing the shared file: ?mode=usb (Quest over adb reverse, http://localhost:8001) or ?mode=tunnel.

export function pickServerUrl(cfg, mode = cfg.mode || "lan") {
  const url = (cfg[`${mode}_url`] || "").trim();
  if (!url || url.includes("<")) throw new Error(`server.json: mode=${mode} but ${mode}_url is not set`);
  return url.replace(/\/$/, "");
}

export async function loadServerUrl(configUrl = "/config/server.json") {
  const q = new URLSearchParams(location.search);
  if (q.get("server")) return q.get("server").replace(/\/$/, "");
  const cfg = await (await fetch(configUrl, { cache: "no-store" })).json();
  return pickServerUrl(cfg, q.get("mode") || cfg.mode);
}

export const wsUrl = (base, path) => base.replace(/^http/, "ws") + path;
