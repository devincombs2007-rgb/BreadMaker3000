"""Authenticated mobile dashboard (FastAPI).

Security model:
* Bound to 127.0.0.1 inside the host; reach it privately via Tailscale (see
  DEPLOYMENT.md). Never published to the internet directly.
* Password verified against a scrypt hash (env DASHBOARD_PASSWORD_HASH, or chosen
  once on first visit at /setup and stored in the private data volume).
* Signed, HttpOnly, SameSite=Strict session cookie (12h). State-changing
  requests additionally require an `X-BM-CSRF` header matching the session.
* Login attempts are rate limited per client IP.
* Read-only views of the database; the only write actions are kill switch
  engage (any session) and release (requires password re-entry).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import psutil
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import SafetyError, load_settings
from ..db import connect
from ..ledger import Ledger
from ..metrics import max_drawdown, sharpe, sortino, trade_stats
from ..orchestrator.heartbeat import worker_status
from ..risk import KillSwitch, RiskEngine
from ..secrets_store import SecretsStore

SESSION_COOKIE = "bm_session"
SESSION_MAX_AGE = 12 * 3600
STATIC = Path(__file__).parent / "static"


# ---- password hashing (stdlib scrypt) ---------------------------------------
def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b64))
    except (ValueError, TypeError):
        return False


class LoginLimiter:
    def __init__(self, max_attempts: int = 5, window: int = 900):
        self.max, self.window = max_attempts, window
        self.attempts: dict[str, list[float]] = defaultdict(list)

    def allowed(self, key: str) -> bool:
        now = time.time()
        self.attempts[key] = [t for t in self.attempts[key] if now - t < self.window]
        return len(self.attempts[key]) < self.max

    def fail(self, key: str) -> None:
        self.attempts[key].append(time.time())


def create_app(db_path: str | None = None, password_hash: str | None = None, secret_key: str | None = None,
               secure_cookies: bool | None = None) -> FastAPI:
    settings = load_settings()
    db_path = db_path or str(settings.db_path)
    store = SecretsStore(Path(db_path).parent)
    fixed_hash = password_hash
    secret_key = secret_key or store.session_key()
    if len(secret_key) < 32:
        raise RuntimeError("session signing key must be >= 32 chars")

    def current_hash() -> str:
        """Password hash, or '' while the dashboard is in first-run setup mode."""
        h = fixed_hash or store.dashboard_hash() or ""
        return h if h.startswith("scrypt$") else ""
    if secure_cookies is None:
        secure_cookies = os.environ.get("DASHBOARD_SECURE_COOKIES", "true").lower() == "true"

    app = FastAPI(title="BreadMaker3000", docs_url=None, redoc_url=None, openapi_url=None)
    signer = URLSafeTimedSerializer(secret_key, salt="bm-session")
    limiter = LoginLimiter()
    kill = KillSwitch(settings.kill_switch_path)

    def db():
        conn = connect(db_path)
        try:
            yield conn
        finally:
            conn.close()

    def session(request: Request) -> dict | None:
        tok = request.cookies.get(SESSION_COOKIE)
        if not tok:
            return None
        try:
            return signer.loads(tok, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return None

    def require(request: Request) -> dict:
        s = session(request)
        if s is None:
            raise HTTPException(401, "login required")
        return s

    def require_csrf(request: Request, s: dict = Depends(require)) -> dict:
        if not hmac.compare_digest(request.headers.get("X-BM-CSRF", ""), s.get("csrf", "")):
            raise HTTPException(403, "bad csrf token")
        return s

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'")
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    def _start_session() -> RedirectResponse:
        tok = signer.dumps({"u": "owner", "csrf": secrets.token_urlsafe(24)})
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, tok, max_age=SESSION_MAX_AGE, httponly=True, secure=secure_cookies,
                        samesite="strict")
        return resp

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page():
        if current_hash():
            return RedirectResponse("/login", status_code=303)
        return (STATIC / "setup.html").read_text()

    @app.post("/setup")
    def setup(password: str = Form(...), password2: str = Form(...)):
        """First-run only: choose the dashboard password. Disabled once a password exists."""
        if current_hash():
            raise HTTPException(409, "password already set")
        if password != password2 or len(password) < 12:
            return RedirectResponse("/setup?error=1", status_code=303)
        if not store.set_dashboard_hash_once(hash_password(password)):
            raise HTTPException(409, "password already set")
        return _start_session()

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        if not current_hash():
            return RedirectResponse("/setup", status_code=303)
        return (STATIC / "login.html").read_text()

    @app.post("/login")
    def login(request: Request, password: str = Form(...)):
        ip = request.client.host if request.client else "?"
        if not limiter.allowed(ip):
            raise HTTPException(429, "too many attempts; wait 15 minutes")
        if not verify_password(password, current_hash()):
            limiter.fail(ip)
            return RedirectResponse("/login?error=1", status_code=303)
        return _start_session()

    @app.post("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        s = session(request)
        if s is None:
            return RedirectResponse("/login" if current_hash() else "/setup", status_code=303)
        return (STATIC / "index.html").read_text().replace("__CSRF__", s["csrf"])

    @app.get("/api/summary")
    def summary(_: dict = Depends(require), conn=Depends(db)):
        return JSONResponse(build_summary(conn, settings, kill))

    @app.post("/api/kill")
    def engage_kill(_: dict = Depends(require_csrf), conn=Depends(db)):
        kill.engage("owner emergency stop via dashboard")
        RiskEngine(settings.risk, kill, conn).log("all", "critical", "kill_switch", "engaged by owner (dashboard)")
        return {"kill_switch": True}

    @app.post("/api/kill/release")
    def release_kill(request: Request, password: str = Form(...), _: dict = Depends(require_csrf), conn=Depends(db)):
        ip = request.client.host if request.client else "?"
        if not limiter.allowed(ip) or not verify_password(password, current_hash()):
            limiter.fail(ip)
            raise HTTPException(403, "password required to release kill switch")
        kill.release()
        RiskEngine(settings.risk, kill, conn).log("all", "warning", "kill_switch", "released by owner (dashboard)")
        return {"kill_switch": False}

    @app.get("/api/settings/alpaca")
    def alpaca_status(_: dict = Depends(require)):
        return store.alpaca_status()  # never returns the keys themselves

    @app.post("/api/settings/alpaca")
    def alpaca_save(request: Request, key: str = Form(...), secret: str = Form(...), password: str = Form(...),
                    _: dict = Depends(require_csrf), conn=Depends(db)):
        ip = request.client.host if request.client else "?"
        if not limiter.allowed(ip) or not verify_password(password, current_hash()):
            limiter.fail(ip)
            raise HTTPException(403, "dashboard password required")
        try:
            store.save_alpaca(key, secret)
        except SafetyError as exc:
            raise HTTPException(400, str(exc)) from exc
        RiskEngine(settings.risk, kill, conn).log("lumibot", "info", "settings", "Alpaca PAPER keys updated by owner")
        return store.alpaca_status()

    return app


def _period_returns(hist: list[tuple[str, float]]) -> dict:
    if not hist:
        return {}
    s = pd.Series({pd.Timestamp(t): e for t, e in hist}).sort_index()
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    now = s.index[-1]
    out = {}
    for label, delta in (("day", timedelta(days=1)), ("week", timedelta(days=7)), ("month", timedelta(days=30))):
        prior = s[s.index <= now - delta]
        base = prior.iloc[-1] if len(prior) else s.iloc[0]
        out[label] = float(s.iloc[-1] / base - 1)
    return out


def _clean(x):
    if isinstance(x, float) and x != x:
        return None
    if isinstance(x, float) and x in (float("inf"), float("-inf")):
        return None
    return x


def build_summary(conn, settings, kill: KillSwitch) -> dict:
    ledger = Ledger(conn)
    engines = []
    for name in ledger.engines():
        s = ledger.summary(name)
        s["asset_class"] = conn.execute("SELECT asset_class FROM portfolios WHERE engine=?", (name,)).fetchone()[0]
        hist = ledger.equity_history(name)
        # Ledger alone has no live prices; the latest worker snapshot is marked to market.
        s["equity_source"] = "cost_basis"
        if hist:
            s["equity"], s["equity_source"], s["marked_at"] = hist[-1][1], "last_snapshot", hist[-1][0]
            s["total_pnl"] = s["equity"] - s["initial_capital"]
            s["total_return"] = s["equity"] / s["initial_capital"] - 1
        eq = pd.Series([e for _, e in hist], dtype=float)
        rets = eq.pct_change().dropna()
        ts = trade_stats(ledger.closed_trade_pnls(name))
        engines.append({
            **{k: _clean(v) for k, v in s.items() if k != "positions"},
            "positions": s["positions"],
            "returns": _period_returns(hist),
            "equity_curve": hist[-500:],
            "sharpe": _clean(sharpe(rets, 365)) if len(rets) > 30 else None,
            "sortino": _clean(sortino(rets, 365)) if len(rets) > 30 else None,
            "max_drawdown": _clean(max_drawdown(eq)) if len(eq) else None,
            "trades": {k: _clean(v) for k, v in ts.items()},
            "benchmark": settings.engines.get(name).benchmark if name in settings.engines else "",
            "recent_fills": ledger.fills(name, limit=20),
        })
    models = [dict(r) for r in conn.execute("SELECT family, version, role, metrics, ts FROM models WHERE role != 'retired'")]
    exps = [dict(r) for r in conn.execute(
        "SELECT family, name, params, oos_sharpe, n_oos_obs, status, ts FROM experiments "
        "WHERE oos_sharpe IS NOT NULL ORDER BY oos_sharpe DESC LIMIT 10")]
    vm = psutil.virtual_memory()
    return {
        "simulated": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kill_switch": {"engaged": kill.engaged(), "reason": kill.reason()},
        "risk_limits": settings.risk.__dict__,
        "combined": {
            "equity": sum(e["equity"] for e in engines),
            "initial": sum(e["initial_capital"] for e in engines),
        },
        "engines": engines,
        "workers": worker_status(conn, settings.raw.get("schedule", {}).get("heartbeat_timeout_seconds", 300)),
        "risk_events": [dict(r) for r in conn.execute("SELECT * FROM risk_events ORDER BY id DESC LIMIT 30")],
        "models": models,
        "strategy_rankings": exps,
        "system": {"cpu_pct": psutil.cpu_percent(interval=None), "mem_pct": vm.percent,
                   "uptime_s": int(time.time() - psutil.boot_time())},
    }
