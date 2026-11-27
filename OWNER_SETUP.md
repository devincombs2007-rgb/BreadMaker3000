# Owner setup (iPhone)

Everything is **simulated**. Never paste passwords or keys into chat.

## Right now: nothing is required

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
4. Merge the pull request. The 4-hourly paper-trading cycle then starts by itself.

The workflow refuses to run Kalshi steps while the repository is public, even if step 3 is done.

## Optional: an always-on dashboard

See HOSTING.md. This is not needed for the GitHub Actions setup.
