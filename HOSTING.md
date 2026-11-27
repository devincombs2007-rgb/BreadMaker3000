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
Artifacts expire after 90 days. An expired artifact halts the chain until the owner explicitly resets it.
If GitHub purges every artifact, "no artifact" can never start a new history by itself: a first run
needs the owner's init phrase. A fallback to an older generation needs the owner's rollback phrase.
The daily health check opens an alert once the newest state is more than 2 days old.

## Private repository: Actions cost controls (no charges possible by default)

GitHub Free includes **2,000 Actions minutes/month** for private repositories, plus 500 MB of
artifact storage. At the limit GitHub **stops the workflows**. Charges are only possible if the
owner adds a payment method **and** sets a budget above $0 (GitHub billing docs). The owner
checklist is in OWNER_SETUP.md.

In-repo controls:

| Control | Effect |
|---|---|
| Disabled Kalshi / Demo workflows use a job-level `if:` on the enable variable | No runner starts while disabled: **0 minutes**. The old guard jobs billed ≥ 1 minute per run, about 360 min/month for both. |
| CI runs on `pull_request` and on pushes to `main` only, and cancels superseded runs | No more double runs (push + pull_request) for every PR commit |
| In a private repo, arm64 and docker-build jobs run on pull requests, not on every push to `main` | Both architectures stay tested before every merge |
| `timeout-minutes` on every job (10–25) | A hung job can't burn hours (tested) |
| Health check `actions-minutes` (private repos) | Sums this repo's billable minutes this month: warning at 60%, alert at 85% |

Estimated private-repo usage with Kalshi paper trading enabled. These are estimates: per-job
rounding to the minute dominates.

| Workflow | Runs | Est. minutes/month |
|---|---|---|
| kalshi-paper | 6/day × ~4 min | ~720 |
| daily-research | 1/day × ~5 min | ~150 |
| forecast-validation | weekly × ~8 min | ~35 |
| CI | per PR push: ~4 jobs × ~2 min | ~8 per push |
| kalshi-demo (if also enabled) | 4/day × ~4 min | ~480 |

Total: about 1,400 min/month plus CI, under the 2,000 included. With both Kalshi cycles and heavy
development it can approach the limit; the health check warns first, and the worst case is paused
workflows, not a bill.

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
