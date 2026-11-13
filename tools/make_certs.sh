#!/usr/bin/env bash
# Regenerate HTTPS certs for localhost + current LAN IP into certs/.
# Re-run at the venue: the LAN IP changes per network.
set -euo pipefail
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
