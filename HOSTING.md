# Free hosting options (investigated 2026-11-26)

Requirement: run the actual Docker stack **24/7** with persistent storage, for **$0**, no paid
plans or subscriptions. Freqtrade needs a long-running process (it cannot be a cron job);
LumiBot's ETF strategy trades at most once per day.

Measured footprint (sandbox, amd64, idle): core 73 MiB, dashboard 77 MiB, Freqtrade 207 MiB at
start-up. Active LumiBot memory is **not measured yet**. Images: Freqtrade 0.31 GB, core 0.16 GB,
LumiBot 0.64 GB (compressed). Disk needed: ~10 GB without LEAN.

| Option | Always-free resources | Fits our stack? | Persistent? | Caveats | Verdict |
|---|---|---|---|---|---|
| **Oracle A1 (Ampere, arm64)** | up to 4 OCPU / 24 GB | Yes, everything | Yes | Ashburn "out of host capacity" at all tried sizes; capacity frees up unpredictably; idle-reclamation policy | Keep retrying occasionally; best target when available |
| **Oracle VM.Standard.E2.1.Micro (AMD)** | 2 VMs × (1/8 OCPU, 1 GB) | Crypto stack yes (micro profile + 4 GB swap); LumiBot unproven | Yes | Very slow CPU (builds/start-up take long); E2 micro is also capacity-limited in some regions; only certain ADs | **Try next: same account, same steps, different shape** |
| **Google Cloud e2-micro** | 1 VM (2 shared vCPU, 1 GB) in us-west1 / us-central1 / us-east1, 30 GB standard disk | Same as Oracle micro | Yes | Needs a billing account (card on file, $0 if within free tier); free tier covers only standard disk in those 3 regions; ~1 GB/month free egress | **Best non-Oracle option** |
| AWS / Azure free tiers | t3.micro / B1s | Yes (1 GB) | Yes | Time-limited (6–12 months / credits) - not "always free" | Not recommended |
| GitHub Actions | 2,000 min/mo (private) / unlimited (public); jobs ≤ 6 h | No 24/7 process | No | Not a host; fine for scheduled **research** (implemented: `research.yml`) and could run a once-daily LumiBot paper rebalance | Use for research only |
| Hugging Face Spaces (free CPU) | 2 vCPU / 16 GB | Docker yes | **No** free persistent storage; sleeps when idle | Loses state; not built for unattended trading bots | No |
| Render / Koyeb / Railway / Fly.io free | small web services | No | Sleep / no free volumes / trial credits | Not always-on with storage | No |

Sources: [Oracle Always Free resources](https://docs.oracle.com/iaas/Content/FreeTier/resourceref.htm),
[Google Cloud free features](https://docs.cloud.google.com/free),
[GitHub arm64 runners in private repos](https://github.blog/changelog/2026-01-29-arm64-standard-runners-are-now-available-in-private-repositories),
[GitHub-hosted runner reference](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).
Facts about third-party plans change; re-check before relying on them.

## Recommendation (in order)

1. **Oracle E2.1.Micro** (no new account): in the instance form choose
   *Change shape → Specialty and previous generation → VM.Standard.E2.1.Micro*. Everything else
   in OWNER_SETUP.md is unchanged; `install.sh` detects the 1 GB RAM and enables
   `docker-compose.micro.yml` + 4 GB swap. Start with crypto (Freqtrade) only; add Alpaca keys later
   and watch memory on the dashboard.
2. **Google Cloud e2-micro** if Oracle micro is also unavailable: Ubuntu 24.04 image, region
   us-east1/us-central1/us-west1, standard persistent disk ≤ 30 GB, paste `deploy/cloud-init.yaml`
   as metadata key `user-data`. No firewall rules needed (Tailscale). Set a $1 budget alert.
3. Keep retrying **Oracle A1** at quiet hours (weekends, early morning US time). When it succeeds,
   restore a backup (DEPLOYMENT.md) and drop the micro profile.

Untested: none of these hosts has run the stack yet. The stack itself is tested on amd64 (sandbox
Docker) and on arm64 (GitHub `ubuntu-24.04-arm` CI job).
