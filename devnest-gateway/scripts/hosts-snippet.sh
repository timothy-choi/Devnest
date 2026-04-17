#!/usr/bin/env bash
# Print /etc/hosts lines for local DevNest gateway testing (run on the host).
# TODO: Replace with dnsmasq or split-DNS when local DNS is automated.

set -euo pipefail

# For same-machine browser + Docker on localhost, prefer compose default ``app.lvh.me`` (no hosts file).
# Use this snippet when you keep ``DEVNEST_BASE_DOMAIN=app.devnest.local`` (or another non-resolving name).
DOMAIN="${DEVNEST_BASE_DOMAIN:-app.devnest.local}"
IP="${DEVNEST_GATEWAY_LOOPBACK_IP:-127.0.0.1}"

echo "# --- DevNest data-plane (Traefik on ${IP}) ---"
echo "${IP}  ws-123.${DOMAIN}"
echo "${IP}  whoami.${DOMAIN}"
echo ""
echo "# Optional: control-plane API (run backend separately, e.g. uvicorn on 8000)"
echo "${IP}  api.devnest.local"
echo ""
echo "# Numeric workspace pattern (future / alternate convention):"
echo "# ${IP}  42.${DOMAIN}"
