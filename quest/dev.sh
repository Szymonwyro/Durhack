#!/usr/bin/env bash
# Quest dev loop over USB.
#   ./quest/dev.sh              serve repo on 127.0.0.1:8001, adb reverse, open ?mock=1&debug=1 in the headset
#   ./quest/dev.sh "?debug=1"   same, with a different query string
#   ./quest/dev.sh sim          run quest/tools/bridge.py --sim (live /tracks path, simulated camera), open ?debug=1
#   ./quest/dev.sh live [cam]   run quest/tools/bridge.py [--camera cam] (real tracks via /ingest or a camera)
#   ./quest/dev.sh stop         kill the server, remove adb reverse/forward rules
set -u
cd "$(dirname "$0")/.."            # repo root
PORT=8001
PIDFILE=/tmp/quest_http.pid

if ! command -v adb >/dev/null; then
  echo "adb not found: brew install android-platform-tools"; exit 1
fi

if [ "${1:-}" = "stop" ]; then
  if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" 2>/dev/null && echo "server stopped"; rm -f "$PIDFILE"
  else echo "no $PIDFILE (server not started by dev.sh)"; fi
  adb reverse --remove tcp:$PORT 2>/dev/null && echo "adb reverse removed"
  adb forward --remove tcp:9222 2>/dev/null && echo "adb forward removed"
  exit 0
fi

SERVER_CMD="python3 quest/serve.py"
QUERY="${1:-?mock=1&debug=1}"
case "${1:-}" in
  sim)  SERVER_CMD="python3 -u quest/tools/bridge.py --sim"; QUERY="?debug=1" ;;
  live) SERVER_CMD="python3 -u quest/tools/bridge.py${2:+ --camera $2}"; QUERY="?debug=1" ;;
esac

# --- exactly one headset in state "device" ---
DEVS=$(adb devices | tail -n +2 | grep -v '^\s*$')
if [ -z "$DEVS" ]; then
  echo "No headset found. Replug the cable / quit Meta Quest Developer Hub, then run adb kill-server"; exit 1
fi
if echo "$DEVS" | grep -q unauthorized; then
  echo "Put the headset on and accept Allow USB debugging"; exit 1
fi
N=$(echo "$DEVS" | grep -c $'\tdevice$')
if [ "$N" -ne 1 ]; then
  echo "Expected exactly one device, got:"; echo "$DEVS"; exit 1
fi
echo "headset: $(echo "$DEVS" | cut -f1)"

# --- server (quest/serve.py = python3 -m http.server + Cache-Control: no-store; or the bridge) ---
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
  echo "port $PORT already listening (pid $(lsof -t -iTCP:$PORT -sTCP:LISTEN | head -1)), reusing it"
  echo "  (to switch server: ./quest/dev.sh stop, then run again)"
else
  nohup $SERVER_CMD >/tmp/quest_http.log 2>&1 &
  echo $! > "$PIDFILE"
  sleep 1
  echo "server started: $SERVER_CMD"
  echo "server started, pid $(cat "$PIDFILE"), log /tmp/quest_http.log"
fi

adb reverse tcp:$PORT tcp:$PORT >/dev/null && echo "adb reverse tcp:$PORT ok"

URL="http://localhost:$PORT/quest/$QUERY"
adb shell am start -a android.intent.action.VIEW -d "'$URL'" com.oculus.browser >/dev/null && echo "opened $URL"

adb forward tcp:9222 localabstract:chrome_devtools_remote >/dev/null && echo "devtools forwarded to localhost:9222"
sleep 1
echo "tabs:"
curl -s http://localhost:9222/json | python3 -c '
import json, sys
try:
    for t in json.load(sys.stdin): print("  -", t.get("type"), t.get("url"))
except Exception as e: print("  (could not read tab list:", e, ")")'

echo "Open chrome://inspect/#devices on the Mac to see the headset console"
echo "Stream it here: python3 quest/tools/console.py   (add --reload to refresh the page)"
