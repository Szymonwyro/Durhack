#!/usr/bin/env bash
# Fallback networking: public HTTPS URL for the fusion server via a cloudflared quick tunnel.
# No account needed and the URL has a valid cert, so it works from any network (venue Wi-Fi, 4G).
# Adds ~50-150 ms: fine for detections/tracks, not for video.
# The URL changes every run. Keep this terminal open; Ctrl+C stops the tunnel.
# Usage: tools/tunnel.sh [local_port]   (default 8000; the server must be running with HTTPS on it)
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${1:-8000}"

if ! command -v cloudflared >/dev/null; then
  echo "cloudflared is not installed. Install it with:"
  echo "  macOS:   brew install cloudflared"
  echo "  Windows: winget install --id Cloudflare.cloudflared"
  echo "  Linux:   https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  exit 1
fi

LOG=$(mktemp -t cloudflared.XXXXXX)
cloudflared tunnel --url "https://localhost:$PORT" --no-tls-verify >"$LOG" 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null || true; rm -f "$LOG"' EXIT INT TERM

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | head -1 || true)
  [ -n "$URL" ] && break
  if ! kill -0 "$PID" 2>/dev/null; then echo "cloudflared exited:"; cat "$LOG"; exit 1; fi
  sleep 1
done
if [ -z "$URL" ]; then echo "No tunnel URL after 60 s:"; tail -20 "$LOG"; exit 1; fi

python3 - "$URL" <<'PY'
import json, sys
from pathlib import Path
p = Path("config/server.json")
cfg = json.loads(p.read_text()) if p.exists() else {"mode": "lan", "lan_url": "", "usb_url": "http://localhost:8000"}
cfg["tunnel_url"] = sys.argv[1]
p.write_text(json.dumps(cfg, indent=2) + "\n")
PY

echo
echo "tunnel_url = $URL   (written to config/server.json)"
echo "Use it:  set \"mode\": \"tunnel\" in config/server.json, or SERVER_URL=$URL, or ?server=$URL in a browser"
echo "Check:   python tools/net_check.py $URL"
echo "Tunnel running (Ctrl+C to stop)..."
wait "$PID"
