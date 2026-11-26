# Deployment (Oracle Cloud Always Free)

Owner-facing steps: [OWNER_SETUP.md](OWNER_SETUP.md). This page is the technical reference.

## Target resources (Oracle Always Free - verify against Oracle's current docs)

| Resource | Always Free allowance | BreadMaker use |
|---|---|---|
| Ampere A1 (arm64) | 4 OCPU + 24 GB RAM total (3,000 OCPU-h / 18,000 GB-h per month) | one VM.Standard.A1.Flex 4/24 runs everything |
| AMD E2.1.Micro | 2 × (1/8 OCPU, 1 GB) | too small for the stack; fallback = Freqtrade + core only, with swap |
| Block storage | 200 GB total (boot + block) | 100 GB boot volume |
| Outbound data | 10 TB / month | well under 1 GB / day |

**Architecture:** all images are multi-arch. Verified tags: `freqtradeorg/freqtrade:2026.10`
(amd64, arm64; 0.31 GB), `…:2026.10_freqai` (0.71 GB), `quantconnect/lean:18294`
(arm64 5.07 GB compressed / amd64 14 GB). `python:3.12-slim-bookworm` is multi-arch.

**Risks:**
- *Capacity:* A1 shapes are often "out of host capacity"; retry later, try another AD, or a smaller shape.
- *Idle reclamation:* Oracle may reclaim Always Free instances that stay idle for 7 days (CPU,
  network and - for A1 - memory utilisation all below ~20%). This workload is light and may
  qualify. Mitigations: FreqAI retraining raises utilisation; Oracle exempts Pay-As-You-Go
  accounts (still $0 within free limits) - **owner decision, not done**. Back up regularly.
- *No SLA:* treat the VM as disposable; state is in one Docker volume + `engines/freqtrade/user_data`.

## Resource budget (Compose limits)

| Service | Mem limit | CPU | Runs |
|---|---|---|---|
| freqtrade | 1 GB | 1.0 | always |
| core (supervisor) | 512 MB | 0.5 | always |
| dashboard | 256 MB | 0.5 | always |
| lumibot | 1 GB | 1.0 | always (idles outside market hours / until keys added) |
| freqai (profile `freqai`) | 3 GB | 1.5 | optional shadow challenger |
| lean (profile `research`) | 6 GB | 2.0 | on demand only |

Steady state ≈ 2.8 GB RAM - comfortable on A1 4/24.

## Install paths

1. **Unattended (recommended):** paste `deploy/cloud-init.yaml` at instance creation. It clones
   the repo, runs `deploy/install.sh`, joins Tailscale with a one-off key, then blanks the key.
   Log: `/var/log/breadmaker-bootstrap.log`.
2. **Manual (SSH):** `git clone … && cd BreadMaker3000 && bash deploy/install.sh && bash deploy/tailscale.sh`.

`install.sh` is idempotent: Docker + Compose from Ubuntu's archive (no `curl | sh`),
random `.env`, 4 GB swap on small VMs, safety check, `docker compose up -d`.
Tailscale is installed from its signed apt repository.

## Operations

```bash
cd /opt/breadmaker
sudo docker compose ps                       # health
sudo docker compose logs -f --tail 100 core  # supervisor / sentinel
git pull && sudo docker compose up -d --build  # update
sudo docker compose --profile freqai up -d   # start FreqAI shadow challenger
```

### On-demand LEAN backtest

```bash
# 1. put daily OHLCV CSVs (date,open,high,low,close,volume; split/dividend adjusted) in a folder, then:
python3 -c "import pandas as pd,glob,os;from breadmaker.integrations.lean_data import write_lean_daily as w
from pathlib import Path
for f in glob.glob('csv/*.csv'):
  d=pd.read_csv(f,parse_dates=['date'],index_col='date'); w(d, Path(f).stem, Path('engines/lean/data'))"
# 2. LEAN also needs its market-hours/symbol-properties folders: copy them from the image once
sudo docker run --rm -v $PWD/engines/lean/data:/out quantconnect/lean:18294 sh -c 'cp -rn /Lean/Data/market-hours /Lean/Data/symbol-properties /out/'
# 3. run
sudo docker compose --profile research run --rm lean
```

## Backup & recovery

```bash
# backup (run weekly; copy the file off the server)
sudo docker run --rm -v breadmaker_bm-data:/data -v $PWD:/b alpine tar czf /b/bm-data-$(date +%F).tgz /data
sudo tar czf ft-userdata-$(date +%F).tgz engines/freqtrade/user_data/*.sqlite
# restore on a fresh VM after install.sh
sudo docker compose down
sudo docker run --rm -v breadmaker_bm-data:/data -v $PWD:/b alpine sh -c 'cd / && tar xzf /b/bm-data-YYYY-MM-DD.tgz'
sudo docker compose up -d
```
Oracle also allows free boot-volume backups (Block Storage → Boot Volumes → Create backup).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard unreachable | Tailscale app connected? `sudo tailscale serve status` |
| freqtrade restarting | `docker compose logs freqtrade` - Kraken reachable? (`curl https://api.kraken.com/0/public/Time`) |
| lumibot "waiting" | Add Alpaca paper keys in dashboard Settings |
| Forgot dashboard password | `sudo docker compose exec dashboard rm /data/secrets/dashboard_password_hash` then reopen dashboard |
