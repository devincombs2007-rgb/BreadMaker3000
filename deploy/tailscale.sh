#!/usr/bin/env bash
# Private HTTPS access to the dashboard from your iPhone via Tailscale (free plan).
# Installs Tailscale from its official signed apt repository (no curl|sh), then serves
# the dashboard ONLY inside your private tailnet. Nothing is exposed to the internet.
#   TS_AUTHKEY=tskey-... bash deploy/tailscale.sh   # unattended (cloud-init)
#   bash deploy/tailscale.sh                         # prints a login link instead
set -euo pipefail
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
. /etc/os-release
$SUDO mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.noarmor.gpg" | $SUDO tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL "https://pkgs.tailscale.com/stable/ubuntu/${VERSION_CODENAME}.tailscale-keyring.list" | $SUDO tee /etc/apt/sources.list.d/tailscale.list >/dev/null
$SUDO apt-get update -y && $SUDO apt-get install -y tailscale
if [ -n "${TS_AUTHKEY:-}" ]; then
  $SUDO tailscale up --ssh=false --hostname=breadmaker --authkey="$TS_AUTHKEY"
else
  echo ">>> Open the login link printed below on your iPhone and sign in (same account as the Tailscale app)."
  $SUDO tailscale up --ssh=false --hostname=breadmaker
fi
$SUDO tailscale serve --bg --https=443 http://127.0.0.1:8000
$SUDO tailscale serve --bg --https=8443 http://127.0.0.1:8080
NAME=$($SUDO tailscale status --json | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
echo "Dashboard: https://${NAME}/"
echo "FreqUI:    https://${NAME}:8443/"
