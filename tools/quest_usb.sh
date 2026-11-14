#!/usr/bin/env bash
# Quest 2 development from Mac-1 over USB (or wireless adb).
#
# The fusion server runs HTTPS on :8000 (LAN) plus plain HTTP on :8001 bound to 127.0.0.1.
# `adb reverse tcp:8001 tcp:8001` makes the Quest's localhost:8001 reach Mac-1's :8001.
# http://localhost counts as a secure context, so WebXR works with no cert.
#
# Usage:
#   tools/quest_usb.sh                      USB: check device, adb reverse, print the URL
#   tools/quest_usb.sh --wireless [ip]      also switch to wireless adb (Quest on the SAME network
#                                           as Mac-1); ip is auto-detected from wlan0 if omitted
#   tools/quest_usb.sh --port 8001          port (default 8001)
# Debug: Chrome on Mac-1 -> chrome://inspect -> the Quest browser's tabs + console.
# Quest Link / Air Link (Windows-only) are NOT needed: WebXR runs in the headset browser.
set -euo pipefail
PORT=8001
WIRELESS=0
QIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --wireless) WIRELESS=1; if [ -n "${2:-}" ] && [ "${2#-}" = "$2" ]; then QIP="$2"; shift; fi ;;
    --port) PORT="$2"; shift ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1 (see --help)"; exit 2 ;;
  esac
  shift
done

if ! command -v adb >/dev/null; then
  echo "adb is not installed. On Mac-1: brew install android-platform-tools"
  echo "(Meta Quest Developer Hub for macOS is optional.)"
  exit 1
fi

adb devices
if adb devices | awk 'NR>1 && $2=="unauthorized"' | grep -q .; then
  echo "Quest is 'unauthorized': put the headset on and accept 'Allow USB debugging' (tick 'Always allow')."
  exit 1
fi
SERIAL=$(adb devices | awk 'NR>1 && $2=="device" {print $1; exit}')
if [ -z "$SERIAL" ]; then
  echo "No Quest found. Check: USB-C DATA cable (not charge-only), Developer Mode on (Meta Horizon iPhone app),"
  echo "and accept 'Allow USB debugging' inside the headset."
  exit 1
fi

if [ "$WIRELESS" = 1 ]; then
  if [ -z "$QIP" ]; then
    QIP=$(adb -s "$SERIAL" shell ip -f inet addr show wlan0 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -1)
  fi
  if [ -z "$QIP" ]; then echo "Couldn't detect the Quest's Wi-Fi IP; pass it: --wireless <quest-ip>"; exit 1; fi
  adb -s "$SERIAL" tcpip 5555
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    if adb connect "$QIP:5555" | grep -q "connected to"; then break; fi
    sleep 1
  done
  SERIAL="$QIP:5555"
  adb devices | grep -q "^$SERIAL[[:space:]]*device" || { echo "adb connect $SERIAL failed (same network? client isolation?)"; exit 1; }
  echo "Wireless adb on $SERIAL - you can unplug the USB cable."
fi

adb -s "$SERIAL" reverse "tcp:$PORT" "tcp:$PORT"
echo "Active reverse forwards:"; adb -s "$SERIAL" reverse --list
echo
echo "Open in the Quest browser:  http://localhost:$PORT/quest/"
echo "(Mac-1's server must serve plain HTTP on 127.0.0.1:$PORT. Debug via chrome://inspect in Chrome on Mac-1.)"
