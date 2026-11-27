#!/usr/bin/env bash
# Private HTTPS access to the dashboard from your iPhone via Tailscale (free plan).
# Installs Tailscale from its official signed apt repository (no curl|sh), then serves
# the dashboard ONLY inside your private tailnet. Nothing is exposed to the internet.
#   TS_AUTHKEY_FILE=/run/key bash deploy/tailscale.sh   # unattended (cloud-init); key read via file:
#   bash deploy/tailscale.sh                             # prints a login link instead
# The auth key is never placed on a command line (visible in `ps`) or printed. Never add `set -x`.
set -euo pipefail
set +x
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
. /etc/os-release
$SUDO mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" | $SUDO tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" | $SUDO tee /etc/apt/sources.list.d/tailscale.list >/dev/null
$SUDO apt-get update -y && $SUDO apt-get install -y tailscale
if [ -n "${TS_AUTHKEY_FILE:-}" ] && [ -s "$TS_AUTHKEY_FILE" ]; then
  $SUDO tailscale up --ssh=false --hostname=breadmaker --auth-key="file:${TS_AUTHKEY_FILE}"
else
  echo ">>> Open the login link printed below on your iPhone and sign in (same account as the Tailscale app)."
  $SUDO tailscale up --ssh=false --hostname=breadmaker
fi
$SUDO tailscale serve --bg --https=443 http://127.0.0.1:8000
NAME=$($SUDO tailscale status --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
echo "Dashboard: https://${NAME}/"
