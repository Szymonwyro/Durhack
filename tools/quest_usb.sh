#!/usr/bin/env bash
# Quest development over USB: `adb reverse` makes the Quest's localhost:PORT reach this machine's PORT.
# http://localhost counts as a secure context, so WebXR works without a trusted cert.
# Usage: tools/quest_usb.sh [port]   (default 8000)
set -euo pipefail
PORT="${1:-8000}"

if ! command -v adb >/dev/null; then
  echo "adb is not installed. Install it with:"
  echo "  macOS:   brew install android-platform-tools"
  echo "  Windows: Meta Quest Developer Hub (bundles adb), or: winget install --id Google.PlatformTools"
  exit 1
fi

adb devices
if adb devices | awk 'NR>1 && $2=="unauthorized"' | grep -q .; then
  echo "Quest is 'unauthorized': put the headset on and accept 'Allow USB debugging' (tick 'Always allow')."
  exit 1
fi
if ! adb devices | awk 'NR>1 && $2=="device"' | grep -q .; then
  echo "No Quest found. Check the USB cable (data, not charge-only) and that Developer Mode is on."
  exit 1
fi

adb reverse "tcp:$PORT" "tcp:$PORT"
echo "Active reverse forwards:"; adb reverse --list
echo
echo "Open in the Quest browser:  http://localhost:$PORT"
echo "(Pages that read config/server.json should use ?mode=usb.)"
echo "If the server on this machine runs TLS on port $PORT, plain http won't connect:"
echo "use https://localhost:$PORT (accept the cert once), or run a second plain-HTTP server for the Quest."
