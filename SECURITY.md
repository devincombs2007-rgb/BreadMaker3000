# Security

## Guarantees enforced in code (and tested)

| Control | Where |
|---|---|
| No real-money mode exists; config `mode` must be `simulation` | `config.load_settings` |
| Freqtrade must be `dry_run: true`, spot, no margin, **no exchange keys** | `config.assert_freqtrade_dry_run`, CI, installer |
| Running Freqtrade bot re-verified every minute; live mode → kill switch + `/stopentry` | `orchestrator/freqtrade_sentinel.py` |
| Alpaca: paper endpoint only, `ALPACA_PAPER=true`, live keys (`AK…`) rejected, broker built with `PAPER: True` | `config.py`, `secrets_store.py`, `run_paper.py` |
| Risk limits can only be tightened (hard ceilings; shorting/leverage disabled) | `config.RiskLimits` |
| Kill switch mounted read-only into trading containers | `docker-compose.yml` |
| No withdrawal permissions are ever requested (no exchange keys at all; Alpaca paper keys only) | design |
| Secret scanning: gitleaks (full history) + repo safety scan in CI | `.github/workflows/ci.yml`, `safety_scan.py` |

## Secrets

- `.env` (internal random secrets) is generated on the server and git-ignored.
- Dashboard password (scrypt) and Alpaca paper keys live in `bm-data:/data/secrets` (dir `0700`,
  files `0600`), entered through the authenticated dashboard. The API never returns keys.
- Never paste keys into chat, GitHub, issues or screenshots.

## Network exposure

- Every container port binds to `127.0.0.1`. No inbound firewall rule is needed or created.
- Phone access is through Tailscale (WireGuard, private to the owner's account), using
  `tailscale serve` for HTTPS. The database, Freqtrade API and admin actions are never public.

## Dashboard

- First visit sets the password once (`/setup` returns 409 afterwards; reachable only inside the tailnet).
- Signed `HttpOnly`, `Secure`, `SameSite=Strict` session cookie (12h); CSRF header on all writes.
- Login rate limit (5 / 15 min / IP). Releasing the kill switch or changing broker keys
  requires re-entering the password.
- CSP, `X-Frame-Options: DENY`, `no-store` caching, no OpenAPI docs exposed.

## Untrusted input

External news, social posts, web content and third-party strategies are treated as data.
No component executes generated or downloaded code. Community strategies are not imported.

## Reporting

Owner: if anything looks wrong, tap **Emergency stop all engines** on the dashboard first.

## Known gaps

- Password reset requires server access (delete `/data/secrets/dashboard_password_hash`).
- Images are pinned by tag, not digest (planned).
- The Tailscale auth key pasted into cloud-init stays in Oracle instance metadata; use a
  **one-off** key so it is useless after first use.
