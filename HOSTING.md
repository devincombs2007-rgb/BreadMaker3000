# Free hosting (no Oracle required) - updated 2026-11-27

The prediction-market workload is **periodic** (one research cycle every few hours; settlements are
daily). It does not need an always-on process, which changes the hosting answer.

| Need | Free option | Status |
|---|---|---|
| Forecast-model validation (public data only) | **GitHub Actions** (`forecast-validation.yml`, weekly, ~10 min) | **Working now** |
| Daily research report, forward forecast log, health checks | **GitHub Actions** (`daily-research.yml`, daily, a few minutes; state carried as a 90-day artifact through the fail-closed state chain) | Built; starts once merged to `main` |
| Kalshi research + paper cycles | **GitHub Actions** (`kalshi-paper.yml`, every 4 h, state carried as a 90-day private artifact via the fail-closed state chain) | Built; **disabled** until (a) Kalshi authorizes and (b) the repo is private |
| Always-on mobile dashboard | Google Cloud e2-micro, Oracle E2.1.Micro, or none (read the Actions run summary in the GitHub app) | Optional |

## Why GitHub Actions is the primary "host"

- **Free:** public repos get unlimited Actions minutes; private repos get 2,000 min/month on the free
  plan. Kalshi cycles at ~3 min × 6/day ≈ 540 min/month plus validation ≈ 40 min/month fit inside
  that.
- **Phone-friendly:** results show in the run summary, readable in the GitHub iPhone app.
- **No server to maintain**, and no capacity lottery.

Limits: scheduled workflows only run from the **default branch**, may start late, and are disabled
after 60 days without repository activity. Runs are capped at 6 h. There is no persistent disk: state is carried as an artifact
by `breadmaker/statechain.py`. The next run finds the newest **non-expired** artifact by name, checks
its SHA-256 manifest and SQLite integrity, and **stops with "STATE LOST"** rather than starting over
if nothing valid is found. A failed run uploads nothing, so the previous good state stays current.
Artifacts expire after 90 days. An expired artifact halts the chain until the owner explicitly resets it. **Known gap:** if GitHub later purges expired artifacts completely, the chain can't tell that apart from a first run and would start fresh. The daily health check flags artifacts that are near expiry well before that.

## Kalshi data constraint (important)

Kalshi's Developer Agreement §3.1 forbids sharing API data with third parties. In a **public**
repository, Actions artifacts and logs are visible to others, so Kalshi cycles must run either:
1. in a **private** repository's Actions (artifacts private), or
2. on a private server (Google Cloud e2-micro, Oracle micro, or a home machine).

The workflow enforces (1) automatically: it refuses to run in a public repository.

## Always-on options, if wanted later

| Option | Always-free resources | Fit | Caveats |
|---|---|---|---|
| Google Cloud e2-micro | 1 VM (2 shared vCPU, 1 GB), us-west1 / us-central1 / us-east1, 30 GB standard disk | Core + dashboard use ~150 MiB idle (measured) | Billing account with a card on file ($0 within limits) |
| Oracle VM.Standard.E2.1.Micro | 2 × (1/8 OCPU, 1 GB) | Same | Capacity-limited in some regions |
| Hugging Face Spaces, Render, Koyeb free | Small containers | Poor | Sleep when idle and/or no free persistent storage |

Sources checked 2026-11-26/27: [Google Cloud free features](https://docs.cloud.google.com/free),
[Oracle Always Free resources](https://docs.oracle.com/iaas/Content/FreeTier/resourceref.htm),
[GitHub Actions billing](https://docs.github.com/en/actions/reference/usage-limits-billing-and-administration).
