# Owner setup (iPhone)

Everything is **simulated**. Never paste passwords or keys into chat.

## Right now: merge PR #3, then start the daily report once (next section)

The forecast models are validated automatically on public data. You can read the results in the
GitHub app: **Actions → forecast-validation → latest run**.

## To let BreadMaker read Kalshi market data (your decision)

Kalshi's Developer Agreement allows API use only to support a member's **own** Kalshi trading, and
forbids sharing the data. You chose to ask Kalshi first.

1. **Ask Kalshi.** Open https://kalshi.com and sign in → **Help/Support** → contact support. Ask:
   *"May I use the public market-data API for a personal, simulation-only research tool that stores
   data privately to inform my own trading?"* Keep their written reply.
2. **If they say yes:** make the repository private: GitHub app → repository → **⋯** → open in browser
   → **Settings** → **Danger Zone** → **Change visibility** → **Private**.
3. Still in **Settings** → **Secrets and variables** → **Actions** → **Variables** tab → **New
   repository variable** → Name `KALSHI_API_ENABLED`, Value `true` → **Add**.
4. Merge the pull request, then run **kalshi-paper** once by hand with the init phrase (see below). After
   that the 4-hourly cycle runs by itself.

The workflow refuses to run Kalshi steps while the repository is public, even if step 3 is done.

## Daily research report (one tap after merging)

1. Merge PR #3.
2. GitHub app → repository → **Actions** → **daily-research** → **Run workflow**. In
   **init_confirmation**, type exactly `start a new simulated history` → **Run**.

   The simulation never starts a history on its own: from the outside, "no saved state" looks the
   same as "GitHub deleted the saved state". This first manual run is your authorization.
3. After that it runs every day at about 13:37 UTC. If anything breaks (including the job failing
   before it writes a report), an issue titled **"BreadMaker health alert"** opens and the GitHub
   app notifies you. It closes itself once everything is healthy again.

## Kalshi Demo (mock money), when you want it

1. Read the Demo terms on Kalshi's site, at https://demo.kalshi.co.
2. Make the repository **private**. Demo data is still Kalshi data.
3. **Settings** → **Secrets and variables** → **Actions** → **Variables** → **New repository variable**:
   Name `KALSHI_DEMO_ENABLED`, Value `true`.
4. Do the billing check below, then run **kalshi-demo** once by hand with the init phrase.

Production Kalshi stays off regardless.

## If a run says "STATE LOST"

The simulation refuses to quietly start over or go back in time. Tell Claude first. The three
phrases below are typed into the workflow's **Run workflow** form, and each covers one situation
only:

| Situation | Field | Exact phrase |
|---|---|---|
| Very first run (no state has ever existed) | init_confirmation | `start a new simulated history` |
| Newest state is broken, and an older one works (lose the newer history) | rollback_confirmation | `continue from the older state and lose the newer history` |
| History exists but nothing is usable; start over | reset_confirmation | `I understand this permanently discards the simulated history` |

## Data licences (only if you want crypto back)

Crypto forecasts are **off**: I couldn't verify the exchanges' market-data terms. If you read, for
example, Coinbase's Market Data Terms and are comfortable with personal research use, go to
**Settings** → **Secrets and variables** → **Actions** → **Variables** and add
`BREADMAKER_DATA_TERMS_ACCEPTED` = `coinbase` (comma-separate more: `coinbase,kraken`). Reports
then say these sources were accepted by you.

## Before making the repository private: make sure Actions can never charge you

Private repositories get **2,000 free Actions minutes a month** on GitHub Free. Beyond that, GitHub
**stops the workflows** unless you have both added a payment method and set a budget above $0.
To keep it that way:

1. Open github.com in a browser → your profile picture → **Settings** → **Billing and licensing**.
2. Check that **no payment method** is on file. If one is, check that the Actions **budget is $0**
   and that it stops usage when reached.
3. Don't buy larger runners.

The daily health check reports this repository's minutes for the month once the repo is private:
a warning at 60%, an alert at 85%. Disabled Kalshi workflows now start no runner at all (0 minutes).

## Optional: an always-on dashboard

See HOSTING.md. This is not needed for the GitHub Actions setup.
