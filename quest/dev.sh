#!/usr/bin/env bash
# Quest dev loop (USB or wireless adb).
#   ./quest/dev.sh              serve repo on 127.0.0.1:8001, adb reverse, open ?mock=1&debug=1 in the headset
#   ./quest/dev.sh "?debug=1"   same, with a different query string
#   ./quest/dev.sh replay       bridge + Role 3's recorded walk (samples/test_walk_camA.jsonl) on a loop, open ?debug=1
#   ./quest/dev.sh sim          bridge --sim (simulated camera), open ?debug=1
#   ./quest/dev.sh live [cam]   bridge [--camera cam] (real detections via /ingest or a local camera), open ?debug=1
#   ./quest/dev.sh wireless     with the USB cable in: switch adb to Wi-Fi, connect, then unplug and keep going
#                               (needs a network without client isolation, e.g. an iPhone hotspot)
#   ./quest/dev.sh stop         kill server / replay / keeper, remove adb reverse/forward rules
# A background keeper re-applies `adb reverse tcp:8001` whenever the headset reconnects (otherwise the page
# shows "GET /world failed" after every cable drop or Wi-Fi blip).
set -u
cd "$(dirname "$0")/.."            # repo root
PORT=8001
PIDFILE=/tmp/quest_http.pid
KEEPER_PID=/tmp/quest_keeper.pid
REPLAY_PID=/tmp/quest_replay.pid

if ! command -v adb >/dev/null; then
  echo "adb not found: brew install android-platform-tools"; exit 1
fi

kill_pidfile() { [ -f "$1" ] && kill "$(cat "$1")" 2>/dev/null && echo "$2 stopped"; rm -f "$1"; }

if [ "${1:-}" = "stop" ]; then
  kill_pidfile "$REPLAY_PID" "replay"
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
