# Security

## Prediction-market controls (current)

| Control | Where |
|---|---|
| No Kalshi order / portfolio / auth / request-signing code; CI scan fails on such patterns | `safety_scan.py`, `tests/test_safety_and_config.py` |
| Kalshi client is GET-only, rejects private paths, allowed hosts only | `kalshi/client.py`, `config.py` |
| Kalshi access OFF unless config flag + written-authorization attestation; workflow refuses public repos | `config.kalshi_access_allowed`, `.github/workflows/kalshi-paper.yml` |
| Kalshi risk parameters can only be tightened (hard bounds) | `config.KalshiConfig` |
| Zero-price fills only allowed as settlement payouts | `ledger.py` |
| No Kalshi credentials exist anywhere (public data needs none) | design |

## Earlier controls (still in force where applicable)

| Control | Where |
|---|---|
| No real-money mode exists; config `mode` must be `simulation` | `config.load_settings` |
| Risk limits can only be tightened (hard ceilings; shorting/leverage disabled) | `config.RiskLimits` |
| Secret scanning: gitleaks (full history) + repo safety scan in CI | `.github/workflows/ci.yml`, `safety_scan.py` |

## Secrets

### Installation credentials (cloud-init) - fixed 2026-11-26
An earlier bootstrap used `set -euxo pipefail`; bash tracing wrote the Tailscale auth key (and the
optional GitHub token) into `/var/log/cloud-init-output.log`, and the token appeared in git's
process arguments. **If you pasted a real key into the old version, revoke it** (Tailscale admin →
Keys; GitHub → Settings → Tokens). The bootstrap now: never traces; moves secrets into 0600 files
in a RAM-backed directory and unsets the variables; authenticates git through environment-only
config; passes the Tailscale key as `--auth-key=file:`; shreds the files and blanks the script's
credentials on exit (also on failure). `tests/test_bootstrap_secrets.py` executes the real
bootstrap (also under `bash -x`) with stub binaries and asserts the credentials never appear in
logs, argv, output, or files left on disk. Residual: the cloud provider keeps the original user-data
in instance metadata - use **one-off** Tailscale keys and **short-lived read-only** GitHub tokens.

### Runtime secrets

- `.env` (internal random secrets) is generated on the server and git-ignored.
- Dashboard password (scrypt) and session key live in `bm-data:/data/secrets` (dir `0700`, files `0600`).
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
- The Tailscale auth key pasted into cloud-init stays in the provider's instance metadata; use a
  **one-off** key so it is useless after first use.
- Risk-enforcement audit of the archived engines: [RISK_AUDIT.md](RISK_AUDIT.md).
