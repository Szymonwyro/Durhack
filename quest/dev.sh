#!/usr/bin/env bash
# Quest dev loop (USB or wireless adb).
#   ./quest/dev.sh              serve repo on 127.0.0.1:8001, adb reverse, open ?mock=1&debug=1 in the headset
#   ./quest/dev.sh "?debug=1"   same, with a different query string
#   ./quest/dev.sh replay       bridge + Role 3's recorded walk (samples/test_walk_camA.jsonl) on a loop, open ?debug=1
#   ./quest/dev.sh sim          bridge --sim (simulated camera), open ?debug=1
#   ./quest/dev.sh live [cam]   bridge [--camera cam] (real detections via /ingest or a local camera), open ?debug=1
#   ./quest/dev.sh wireless     with the USB cable in: switch adb to Wi-Fi, connect, then unplug and keep going
#                               (needs a network without client isolation, e.g. an iPhone hotspot)
#   ./quest/dev.sh --untethered [fusion-ws-url]
#                               Wi-Fi, no cable: bridge also on https://<mac-ip>:8443 (self-signed, quest/.certs/),
#                               live feed from the fusion server (default: first ws://<ip>:<port>/tracks in README.md) via
#                               tools/fusion_to_quest.py, everything in restart loops. Route A = Quest -> Mac over
#                               the venue Wi-Fi; falls back to Route B = cloudflared quick tunnel. Prints URL + QR.
#   ./quest/dev.sh stop         kill server / replay / feed / tunnel / keeper, remove adb reverse/forward rules
# A background keeper re-applies `adb reverse tcp:8001` whenever the headset reconnects (otherwise the page
# shows "GET /world failed" after every cable drop or Wi-Fi blip).
set -u
cd "$(dirname "$0")/.."            # repo root
PORT=8001
PIDFILE=/tmp/quest_http.pid
KEEPER_PID=/tmp/quest_keeper.pid
REPLAY_PID=/tmp/quest_replay.pid
FUSION_PID=/tmp/quest_fusion.pid
TUNNEL_PID=/tmp/quest_tunnel.pid
TLS_PORT=8443

if ! command -v adb >/dev/null; then
  echo "adb not found: brew install android-platform-tools"; exit 1
fi

kill_pidfile() { [ -f "$1" ] && { pkill -P "$(cat "$1")" 2>/dev/null; kill "$(cat "$1")" 2>/dev/null && echo "$2 stopped"; }; rm -f "$1"; }

if [ "${1:-}" = "stop" ]; then
  kill_pidfile "$REPLAY_PID" "replay"
  kill_pidfile "$FUSION_PID" "fusion feed"
  kill_pidfile "$TUNNEL_PID" "tunnel"
  kill_pidfile "$KEEPER_PID" "keeper"
  kill_pidfile "$PIDFILE" "server"
  adb reverse --remove-all 2>/dev/null && echo "adb reverse removed"
  adb forward --remove tcp:9222 2>/dev/null && echo "adb forward removed"
  exit 0
fi

usb_serial()  { adb devices | awk 'NR>1 && $2=="device" && $1 !~ /:/ {print $1; exit}'; }
wifi_serial() { adb devices | awk 'NR>1 && $2=="device" && $1 ~ /:/ {print $1; exit}'; }

if [ "${1:-}" = "wireless" ]; then
  S=$(usb_serial)
  [ -z "$S" ] && { echo "Plug the headset in by USB first (wireless setup needs it once)."; exit 1; }
  IP=$(adb -s "$S" shell ip -f inet addr show wlan0 | awk '/inet /{sub(/\/.*/,"",$2); print $2}')
  [ -z "$IP" ] && { echo "Headset has no Wi-Fi address: join the same Wi-Fi as this Mac."; exit 1; }
  echo "headset Wi-Fi $IP, this Mac $(ipconfig getifaddr en0 2>/dev/null || echo '?')"
  adb -s "$S" tcpip 5555 >/dev/null; sleep 3
  if ! nc -z -G 3 "$IP" 5555 2>/dev/null; then
    echo "Can't reach the headset at $IP:5555 - this Wi-Fi blocks device-to-device traffic (client isolation)."
    echo "Fix: put the Mac and the Quest on an iPhone Personal Hotspot (Maximize Compatibility on), then rerun."
    echo "Staying on USB for now."
    exit 1
  fi
  adb connect "$IP:5555"
  echo "Wireless adb connected. You can unplug the cable; run ./quest/dev.sh [mode] as usual."
  exit 0
fi

if [ "${1:-}" = "--untethered" ]; then
  MAC=$(ipconfig getifaddr en0 || ipconfig getifaddr en1)
  [ -z "$MAC" ] && { echo "This Mac has no Wi-Fi IPv4 address."; exit 1; }
  FUSION="${2:-}"
  if [ -z "$FUSION" ]; then
    FUSION=$(grep -m1 -oE 'ws://[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+:[0-9]+/tracks' README.md)  # README "Live server"
  fi
  echo "Mac Wi-Fi: $MAC   fusion server: ${FUSION:-none (no live feed)}"
  USB=$(usb_serial)
  if [ -n "$USB" ]; then
    Q=$(adb -s "$USB" shell ip -f inet addr show wlan0 | awk '/inet /{sub(/\/.*/,"",$2); print $2}')
    echo "Quest Wi-Fi: ${Q:-NONE - join the Quest to the same Wi-Fi as this Mac}"
  fi

  # certificate for the current IP (regenerated when the network changes)
  if [ "$(cat quest/.certs/ip 2>/dev/null)" != "$MAC" ]; then
    mkdir -p quest/.certs
    openssl req -x509 -newkey rsa:2048 -nodes -days 397 -keyout quest/.certs/key.pem -out quest/.certs/cert.pem \
      -subj "/CN=quest-bridge" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$MAC" \
      -addext "extendedKeyUsage=serverAuth" -addext "basicConstraints=critical,CA:FALSE" 2>/dev/null \
      && echo "$MAC" > quest/.certs/ip && echo "new self-signed cert for $MAC (accept it once in the Quest browser)"
  fi

  # bridge (HTTP 127.0.0.1:8001 for USB + HTTPS 0.0.0.0:8443 for Wi-Fi), restarted if it dies
  if ! curl -sk -o /dev/null "https://127.0.0.1:$TLS_PORT/world"; then
    kill_pidfile "$REPLAY_PID" "replay"; kill_pidfile "$PIDFILE" "old server"
    P=$(lsof -t -iTCP:$PORT -sTCP:LISTEN | head -1); [ -n "$P" ] && kill "$P"
    sleep 0.5
    nohup bash -c "while true; do python3 -u quest/tools/bridge.py --tls-port $TLS_PORT; sleep 2; done" >/tmp/quest_http.log 2>&1 &
    echo $! > "$PIDFILE"; sleep 2
  fi
  echo "bridge: http://127.0.0.1:$PORT (USB) + https://$MAC:$TLS_PORT (Wi-Fi)   log /tmp/quest_http.log"

  # live feed: fusion server -> bridge
  if [ -n "$FUSION" ] && ! { [ -f "$FUSION_PID" ] && kill -0 "$(cat "$FUSION_PID")" 2>/dev/null; }; then
    nohup bash -c "while true; do python3 -u tools/fusion_to_quest.py $FUSION --margin 0.2; sleep 2; done" >/tmp/quest_fusion.log 2>&1 &
    echo $! > "$FUSION_PID"
  fi
  [ -n "$FUSION" ] && echo "live feed: $FUSION -> bridge   log /tmp/quest_fusion.log"

  # Route A: can the Quest reach the Mac? (testable only while USB is attached; assumed otherwise)
  ROUTE=A
  if [ -n "$USB" ]; then
    adb -s "$USB" shell "printf '' | toybox nc -w 5 $MAC $TLS_PORT" >/dev/null 2>&1 || ROUTE=B
  fi
  URL="https://$MAC:$TLS_PORT/quest/?debug=1"
  if [ "$ROUTE" = B ]; then
    echo "Route A blocked (Quest can't reach $MAC:$TLS_PORT) - trying Route B, cloudflared quick tunnel"
    command -v cloudflared >/dev/null || { echo "cloudflared missing: brew install cloudflared"; exit 1; }
    kill_pidfile "$TUNNEL_PID" "old tunnel"
    nohup bash -c "while true; do cloudflared tunnel --no-autoupdate --protocol http2 --url http://localhost:$PORT; sleep 5; done" \
      >/tmp/cloudflared.log 2>&1 &
    echo $! > "$TUNNEL_PID"
    T=""
    for _ in $(seq 1 40); do
      T=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log | grep -v '//api\.' | head -1)
      [ -n "$T" ] && break; sleep 1
    done
    [ -z "$T" ] && { echo "Route B failed too (this network blocks trycloudflare.com). Use an iPhone hotspot."; tail -2 /tmp/cloudflared.log; exit 1; }
    URL="$T/quest/?debug=1"
  fi
  echo "ROUTE $ROUTE   URL $URL"
  if [ -n "$USB" ]; then
    adb -s "$USB" shell am start -a android.intent.action.VIEW -d "'$URL'" com.oculus.browser >/dev/null 2>&1 \
      && echo "opened on the Quest$([ "$ROUTE" = A ] && echo ' - accept the certificate warning once (Advanced -> Proceed)')"
  fi
  if command -v qrencode >/dev/null; then qrencode -t ansiutf8 "$URL"; else echo "(brew install qrencode to get a QR code here)"; fi
  echo "Headset status: tail -f logs/quest.log   (entries carry 'via' = route)"
  exit 0
fi

SERVER_CMD="python3 quest/serve.py"
QUERY="${1:-?mock=1&debug=1}"
REPLAY=0
case "${1:-}" in
  replay) SERVER_CMD="python3 -u quest/tools/bridge.py"; QUERY="?debug=1"; REPLAY=1 ;;
  sim)    SERVER_CMD="python3 -u quest/tools/bridge.py --sim"; QUERY="?debug=1" ;;
  live)   SERVER_CMD="python3 -u quest/tools/bridge.py${2:+ --camera $2}"; QUERY="?debug=1" ;;
esac

# --- headset: prefer wireless if connected (survives unplugging), else USB ---
DEVS=$(adb devices | tail -n +2 | grep -v '^\s*$')
if echo "$DEVS" | grep -q unauthorized; then echo "Put the headset on and accept Allow USB debugging"; exit 1; fi
SERIAL=$(wifi_serial); [ -z "$SERIAL" ] && SERIAL=$(usb_serial)
if [ -z "$SERIAL" ]; then
  echo "No headset found. Replug the cable / quit Meta Quest Developer Hub, then run adb kill-server"; exit 1
fi
export ANDROID_SERIAL="$SERIAL"
echo "headset: $SERIAL"

# --- server (quest/serve.py = python3 -m http.server + Cache-Control: no-store; or the bridge) ---
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT already listening (pid $(lsof -t -iTCP:$PORT -sTCP:LISTEN | head -1)), reusing it"
  echo "  (to switch server: ./quest/dev.sh stop, then run again)"
else
  nohup $SERVER_CMD >/tmp/quest_http.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
  echo "server started: $SERVER_CMD (pid $(cat "$PIDFILE"), log /tmp/quest_http.log)"
fi

# --- recorded walk on a loop (restarts if the bridge restarts) ---
if [ "$REPLAY" = 1 ] && ! { [ -f "$REPLAY_PID" ] && kill -0 "$(cat "$REPLAY_PID")" 2>/dev/null; }; then
  nohup bash -c "while true; do python3 tools/test_walk.py replay --url ws://127.0.0.1:$PORT/ingest; sleep 2; done" \
    >/tmp/quest_replay.log 2>&1 &
  echo $! > "$REPLAY_PID"
  echo "replaying samples/test_walk_camA.jsonl on a loop (log /tmp/quest_replay.log)"
fi

# --- keeper: re-apply reverse + devtools forward whenever the headset (re)connects ---
if ! { [ -f "$KEEPER_PID" ] && kill -0 "$(cat "$KEEPER_PID")" 2>/dev/null; }; then
  nohup bash -c '
    while true; do
      for d in $(adb devices | awk "NR>1 && \$2==\"device\" {print \$1}"); do
        if ! adb -s "$d" reverse --list 2>/dev/null | grep -q "tcp:'$PORT'"; then
          adb -s "$d" reverse tcp:'$PORT' tcp:'$PORT' >/dev/null 2>&1 && echo "$(date +%T) reverse restored on $d"
        fi
      done
      S=$(adb devices | awk "NR>1 && \$2==\"device\" && \$1 ~ /:/ {print \$1; exit}")
      [ -z "$S" ] && S=$(adb devices | awk "NR>1 && \$2==\"device\" {print \$1; exit}")
      if [ -n "$S" ] && ! adb forward --list | grep -q "^$S tcp:9222"; then
        adb forward --remove tcp:9222 >/dev/null 2>&1
        adb -s "$S" forward tcp:9222 localabstract:chrome_devtools_remote >/dev/null 2>&1 && echo "$(date +%T) devtools -> $S"
      fi
      sleep 2
    done' >/tmp/quest_keeper.log 2>&1 &
  echo $! > "$KEEPER_PID"
  echo "keeper started (re-applies adb reverse on reconnect; log /tmp/quest_keeper.log)"
fi

adb reverse tcp:$PORT tcp:$PORT >/dev/null && echo "adb reverse tcp:$PORT ok"

URL="http://localhost:$PORT/quest/$QUERY"
OUT=$(adb shell am start -a android.intent.action.VIEW -d "'$URL'" com.oculus.browser 2>&1)
if echo "$OUT" | grep -q Error; then
  echo "could not open the page (headset still booting? retry in a few seconds):"; echo "$OUT"; exit 1
fi
echo "opened $URL"

adb forward --remove tcp:9222 >/dev/null 2>&1
adb forward tcp:9222 localabstract:chrome_devtools_remote >/dev/null && echo "devtools forwarded to localhost:9222"
sleep 2
echo "tabs:"
curl -s http://localhost:9222/json | python3 -c '
import json, sys
try:
    for t in json.load(sys.stdin): print("  -", t.get("type"), t.get("url"))
except Exception as e: print("  (could not read tab list yet:", e, ")")'

echo "Open chrome://inspect/#devices on the Mac to see the headset console"
echo "Stream it here: python3 quest/tools/console.py   (add --reload to refresh the page)"
