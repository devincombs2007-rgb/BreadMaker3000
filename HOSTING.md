# Free hosting (no Oracle required) - updated 2026-11-27

The prediction-market workload is **periodic** (one research cycle every few hours; settlements are
daily). It does not need an always-on process, which changes the hosting answer.

| Need | Free option | Status |
|---|---|---|
| Forecast-model validation (public data only) | **GitHub Actions** (`forecast-validation.yml`, weekly, ~10 min) | **Working now** |
| Kalshi research + paper cycles | **GitHub Actions** (`kalshi-paper.yml`, every 4 h, state carried as a 14-day private artifact) | Built; **disabled** until (a) Kalshi authorizes and (b) the repo is private |
| Always-on mobile dashboard | Google Cloud e2-micro, Oracle E2.1.Micro, or none (read the Actions run summary in the GitHub app) | Optional |

## Why GitHub Actions is the primary "host"

- **Free:** public repos get unlimited Actions minutes; private repos get 2,000 min/month on the free
  plan. Kalshi cycles at ~3 min × 6/day ≈ 540 min/month plus validation ≈ 40 min/month fit inside
  that.
- **Phone-friendly:** results show in the run summary, readable in the GitHub iPhone app.
- **No server to maintain**, and no capacity lottery.

Limits: scheduled workflows only run from the **default branch**, may start late, and are disabled
after 60 days without repository activity. Runs are capped at 6 h. There is no persistent disk: the
paper-trading state is carried as an artifact, and if a run fails the next one resumes from the last
successful state.

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
