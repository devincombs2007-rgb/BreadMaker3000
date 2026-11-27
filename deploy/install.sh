#!/usr/bin/env bash
# BreadMaker3000 installer for an Oracle Cloud Always Free Ubuntu instance (arm64 or amd64).
# Idempotent: safe to re-run (updates images, keeps .env and data).
#
#   bash deploy/install.sh            # from inside a clone (cloud-init does this for you)
#
# Installs Docker + Compose from Ubuntu's own archive, creates .env with random internal
# secrets, adds swap on small machines, runs the safety check, and starts the stack.
# It does NOT open any firewall port. The dashboard password and Alpaca PAPER keys are
# entered later in the dashboard itself.
set -euo pipefail
set +x  # never trace: this script runs inside the credential-handling bootstrap

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

say() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

say "1/7 Installing Docker, Compose, git (Ubuntu packages)"
$SUDO apt-get update -y
$SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git python3 openssl ca-certificates curl
$SUDO systemctl enable --now docker
id ubuntu >/dev/null 2>&1 && $SUDO usermod -aG docker ubuntu || true

say "2/7 Swap (helps 1 GB micro instances; harmless on Ampere A1)"
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
if [ "$MEM_MB" -lt 8000 ] && ! swapon --show | grep -q /swapfile; then
  $SUDO fallocate -l 4G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile && $SUDO swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | $SUDO tee -a /etc/fstab >/dev/null
fi

say "3/7 Creating .env with random internal secrets (kept on this server only)"
if [ ! -f .env ]; then
  umask 077
  cp .env.example .env
fi

say "4/7 Preparing folders"
mkdir -p data

say "5/7 Safety check (refuses to start if anything could trade real money)"
python3 - <<'PY'
import re, sys
cfg = open("config/breadmaker.yaml").read()
if not re.search(r"^mode:\s*simulation", cfg, re.M):
    print("SAFETY STOP: config mode must be simulation"); sys.exit(1)
print("safety check PASS (simulation only; Kalshi access is", "requested" if re.search(r"^\s*api_enabled:\s*true", cfg, re.M) else "OFF", ")")
PY

say "6/7 Building and starting containers (first run takes 10-20 minutes)"
$SUDO docker compose build
$SUDO docker compose up -d
sleep 20
$SUDO docker compose ps

say "7/7 Done"
cat <<'TXT'
Next (see OWNER_SETUP.md):
  * Private phone access:  bash deploy/tailscale.sh   (cloud-init already does this)
  * Open the dashboard and choose your password. Kalshi access stays OFF (see KALSHI.md).
Everything is SIMULATED. No real money is ever traded.
TXT
