#!/usr/bin/env bash
# How to make iPhones trust the fusion server's HTTPS cert.
# On iOS, tapping through a self-signed warning can still leave wss:// WebSockets broken,
# so use either (a) the cloudflared tunnel URL or (b) mkcert's root CA installed on each iPhone.
set -euo pipefail
echo "iPhones + HTTPS: tapping through a self-signed warning can still break wss:// WebSockets."
echo
echo "Option A (no setup on the phone): use the cloudflared tunnel URL (real cert)"
echo "  tools/tunnel.sh   -> open the printed https://*.trycloudflare.com URL in Safari"
echo
echo "Option B: trust mkcert's root CA on each iPhone (then https://<mac1-ip>:8000 works with no warning)"
if command -v mkcert >/dev/null; then
  CAROOT=$(mkcert -CAROOT)
  echo "  Root CA file: $CAROOT/rootCA.pem"
  [ -f "$CAROOT/rootCA.pem" ] || echo "  (not created yet - run tools/make_certs.sh first)"
  echo "  Reveal it in Finder for AirDrop:  open -R \"$CAROOT/rootCA.pem\""
else
  echo "  mkcert not installed: brew install mkcert nss, then tools/make_certs.sh"
fi
cat <<'STEPS'
  1. AirDrop rootCA.pem to the iPhone (accept it; "Profile Downloaded" appears).
  2. Settings -> Profile Downloaded (or General -> VPN & Device Management) -> Install -> enter passcode -> Install.
  3. Settings -> General -> About -> Certificate Trust Settings -> enable full trust for the "mkcert ..." root.
  4. Test: open https://<mac1-ip>:8000/world in Safari - no warning means it worked.
  Only do this with this team's own Mac-generated CA, and remove the profile after the event
  (General -> VPN & Device Management -> mkcert ... -> Remove Profile).
STEPS
