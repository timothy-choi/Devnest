#!/usr/bin/env bash
# Print /etc/hosts lines for local DevNest gateway testing (run on the host, not in Docker).
# TODO: Replace with dnsmasq or split-DNS when local DNS is automated.

set -euo pipefail

DOMAIN="${DEVNEST_BASE_DOMAIN:-app.devnest.local}"
IP="${DEVNEST_GATEWAY_LOOPBACK_IP:-127.0.0.1}"

echo "# Add to /etc/hosts (example workspace id=1):"
echo "${IP}  1.${DOMAIN}"
echo "# Backend API (if running on host port 8000):"
echo "${IP}  api.devnest.local"
