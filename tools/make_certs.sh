#!/usr/bin/env bash
# Regenerate HTTPS certs for localhost + this machine's current LAN IP into certs/.
#
# RE-RUN THIS WHENEVER THE NETWORK CHANGES (new hotspot, hotspot restarted, venue Wi-Fi):
# the LAN IP changes, and a cert for the old IP fails on iPhones and the Quest.
# Afterwards, update lan_url in config/server.json. Not needed for tools/tunnel.sh
# (valid public cert) or tools/quest_usb.sh (localhost).
# Usage: tools/make_certs.sh [--help]
set -euo pipefail
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0; fi
cd "$(dirname "$0")/.."
IP=$(.venv/bin/python tools/lan_ip.py | head -1)
mkdir -p certs
if command -v mkcert >/dev/null; then
  mkcert -install
  mkcert -cert-file certs/cert.pem -key-file certs/key.pem localhost 127.0.0.1 "$IP"
  echo "mkcert certs: certs/cert.pem certs/key.pem  (root CA: $(mkcert -CAROOT)/rootCA.pem)"
else
  echo "mkcert not found (brew install mkcert nss) - using openssl self-signed fallback"
fi
openssl req -x509 -newkey rsa:2048 -nodes -days 397 \
  -keyout certs/selfsigned-key.pem -out certs/selfsigned.pem -subj "/CN=durhack-dev" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:$IP" \
  -addext "extendedKeyUsage=serverAuth" -addext "basicConstraints=critical,CA:FALSE" 2>/dev/null
echo "self-signed: certs/selfsigned.pem certs/selfsigned-key.pem  for https://$IP:8000"
echo "Next: set \"lan_url\": \"https://$IP:8000\" in config/server.json. Re-run this script whenever the hotspot/network changes."
echo "iPhones: tools/iphone_cert_help.sh (trust the mkcert root CA, or use the tunnel URL)"
