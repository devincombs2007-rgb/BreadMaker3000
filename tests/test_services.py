from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from breadmaker.dashboard.app import create_app, hash_password, verify_password
from breadmaker.db import connect
from breadmaker.ledger import Ledger
from breadmaker.orchestrator.freqtrade_sentinel import mirror_trades, run_once
from breadmaker.orchestrator.heartbeat import beat, worker_status
from breadmaker.orchestrator.market_hours import is_market_open, is_trading_day, nyse_holidays
from breadmaker.reports.generate import build_report
from breadmaker.risk import KillSwitch, RiskEngine

PW = "correct horse battery staple"


# ---- market hours -----------------------------------------------------------
def test_nyse_holidays_2026():
    h = nyse_holidays(2026)
    for d in [date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3), date(2026, 5, 25),
              date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7), date(2026, 11, 26), date(2026, 12, 25)]:
        assert d in h, d
    assert len(h) == 10


def test_market_open_rules():
    assert is_market_open(datetime(2026, 11, 24, 15, 0, tzinfo=timezone.utc))      # Tue 10:00 ET
    assert not is_market_open(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc))  # Thanksgiving
    assert not is_market_open(datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc))  # early close 13:00 ET
    assert not is_market_open(datetime(2026, 11, 28, 15, 0, tzinfo=timezone.utc))  # Saturday
    assert not is_trading_day(date(2026, 4, 3))                                       # Good Friday


# ---- sentinel ---------------------------------------------------------------
class FakeFT:
    def __init__(self, dry_run=True, equity_rate=100.0):
        self.dry_run, self.rate, self.stopped = dry_run, equity_rate, 0

    def ping(self):
        return True

    def show_config(self):
        return {"dry_run": self.dry_run, "trading_mode": "spot", "state": "running"}

    def trades(self, limit=500, offset=0):
        if offset:
            return {"trades": []}
        return {"trades": [
            {"trade_id": 1, "pair": "BTC/USD", "amount": 1.0, "open_rate": 100.0, "fee_open_cost": 0.4,
             "is_open": False, "close_rate": 105.0, "fee_close_cost": 0.42, "open_timestamp": 1, "close_timestamp": 2,
             "open_date": "2026-01-01 00:00:00", "close_date": "2026-01-01 05:00:00"},
            {"trade_id": 2, "pair": "ETH/USD", "amount": 0.5, "open_rate": 100.0, "fee_open_cost": 0.2,
             "is_open": True, "open_timestamp": 3, "open_date": "2026-01-01 06:00:00"}]}

    def status(self):
        return [{"trade_id": 2, "pair": "ETH/USD", "amount": 0.5, "open_rate": 100.0, "fee_open_cost": 0.2,
                 "is_open": True, "open_timestamp": 3, "open_date": "2026-01-01 06:00:00", "current_rate": self.rate}]

    def stopentry(self):
        self.stopped += 1
        return {}


@pytest.fixture
def risk(conn, tmp_path, tmp_settings):
    Ledger(conn).ensure_portfolio("freqtrade", "crypto", 1000)
    return RiskEngine(tmp_settings.risk, KillSwitch(tmp_path / "KILL"), conn)


def test_sentinel_mirrors_and_is_idempotent(conn, risk):
    ft = FakeFT()
    r = run_once(ft, conn, risk, {})
    assert r["ok"] and not r["breaches"]
    run_once(ft, conn, risk, {})
    L = Ledger(conn)
    assert len(L.fills("freqtrade")) == 3
    s = L.summary("freqtrade", {"ETH/USD": 100.0})
    assert s["realized_pnl_net_of_fees"] == pytest.approx(5 - 0.82 - 0.2)


def test_sentinel_kills_on_live_mode(conn, risk):
    ft = FakeFT(dry_run=False)
    r = run_once(ft, conn, risk, {})
    assert r.get("fatal") and risk.kill_switch.engaged() and ft.stopped == 1


def test_sentinel_drawdown_halts(conn, risk):
    ft = FakeFT(equity_rate=100.0)
    st = {"peak": 1100.0}
    r = run_once(ft, conn, risk, st)
    assert any("drawdown" in b for b in r["breaches"]) and r["halted"] and ft.stopped >= 1
    assert risk.halts.active("freqtrade", datetime.now(timezone.utc))["kind"] == "drawdown"


def test_mirror_orders_exit_before_entry(conn):
    L = Ledger(conn)
    L.ensure_portfolio("freqtrade", "crypto", 100)
    trades = [
        {"trade_id": 2, "pair": "B", "amount": 0.9, "open_rate": 100, "is_open": True, "open_timestamp": 10},
        {"trade_id": 1, "pair": "A", "amount": 0.9, "open_rate": 100, "is_open": False, "close_rate": 101,
         "open_timestamp": 1, "close_timestamp": 5},
    ]
    assert mirror_trades(L, trades) == 3


# ---- heartbeats / reports -----------------------------------------------------
def test_heartbeat_staleness(conn):
    beat(conn, "w1")
    assert worker_status(conn, 300)[0]["status"] == "running"
    future = datetime.now(timezone.utc) + pd.Timedelta(minutes=10)
    assert worker_status(conn, 300, now=future)[0]["status"] == "stale"


def test_report_has_banner_and_losses(conn):
    L = Ledger(conn)
    L.ensure_portfolio("freqtrade", "crypto", 1000)
    L.record_fill("freqtrade", "BTC/USD", "buy", 1, 100, 0, "a")
    L.record_fill("freqtrade", "BTC/USD", "sell", 1, 50, 0, "b")
    rep = build_report(conn, "daily")
    assert "SIMULATED RESULTS ONLY" in rep and "LOSS" in rep


# ---- dashboard ----------------------------------------------------------------
@pytest.fixture
def client(tmp_settings):
    app = create_app(db_path=str(tmp_settings.db_path), password_hash=hash_password(PW),
                     secret_key="x" * 40, secure_cookies=False)
    Ledger(connect(tmp_settings.db_path)).ensure_portfolio("freqtrade", "crypto", 1000)
    return TestClient(app)


def test_password_hashing():
    h = hash_password(PW)
    assert verify_password(PW, h) and not verify_password("nope", h) and not verify_password(PW, "garbage")


def test_dashboard_requires_auth(client):
    assert client.get("/api/summary").status_code == 401
    assert client.get("/", follow_redirects=False).status_code == 303
    assert client.get("/healthz").status_code == 200


def test_dashboard_login_and_kill_switch_flow(client, tmp_settings):
    assert client.post("/login", data={"password": "wrong"}, follow_redirects=False).headers["location"] == "/login?error=1"
    r = client.post("/login", data={"password": PW}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    page = client.get("/").text
    assert "SIMULATED" in page
    csrf = page.split('const CSRF="')[1].split('"')[0]
    data = client.get("/api/summary").json()
    assert data["simulated"] is True and data["engines"][0]["engine"] == "freqtrade"
    assert data["engines"][0]["asset_class"] == "crypto"
    assert client.post("/api/kill").status_code == 403  # no CSRF header
    assert client.post("/api/kill", headers={"X-BM-CSRF": csrf}).json() == {"kill_switch": True}
    assert KillSwitch(tmp_settings.kill_switch_path).engaged()
    assert client.post("/api/kill/release", headers={"X-BM-CSRF": csrf}, data={"password": "bad"}).status_code == 403
    from breadmaker.risk import EngineHalts
    EngineHalts(tmp_settings.data_dir).halt("freqtrade", "drawdown", "dd", datetime.now(timezone.utc), None)
    assert client.get("/api/summary").json()["halts"]["freqtrade"]["kind"] == "drawdown"
    r = client.post("/api/kill/release", headers={"X-BM-CSRF": csrf}, data={"password": PW})
    assert r.status_code == 200 and r.json()["halts_released"] == ["freqtrade"]
    assert not KillSwitch(tmp_settings.kill_switch_path).engaged()
    assert (tmp_settings.data_dir / "PEAK_RESET").exists()


def test_login_rate_limited(client):
    for _ in range(5):
        client.post("/login", data={"password": "wrong"}, follow_redirects=False)
    assert client.post("/login", data={"password": PW}, follow_redirects=False).status_code == 429


def test_security_headers(client):
    h = client.get("/login").headers
    assert h["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in h["content-security-policy"]


# ---- first-run setup + Alpaca settings ------------------------------------------
@pytest.fixture
def fresh_client(tmp_settings, monkeypatch):
    monkeypatch.delenv("DASHBOARD_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    return TestClient(create_app(db_path=str(tmp_settings.db_path), secure_cookies=False))


def test_first_run_setup_then_locked(fresh_client):
    c = fresh_client
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    assert c.get("/login", follow_redirects=False).headers["location"] == "/setup"
    r = c.post("/setup", data={"password": "short", "password2": "short"}, follow_redirects=False)
    assert r.headers["location"] == "/setup?error=1"
    r = c.post("/setup", data={"password": PW, "password2": PW}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/api/summary").status_code == 200  # auto-logged-in
    # setup can never run twice (no takeover)
    assert c.post("/setup", data={"password": "x" * 20, "password2": "x" * 20}).status_code == 409
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_alpaca_settings_paper_only_and_never_returned(fresh_client, tmp_settings):
    from breadmaker.secrets_store import SecretsStore
    c = fresh_client
    c.post("/setup", data={"password": PW, "password2": PW})
    csrf = c.get("/").text.split('const CSRF="')[1].split('"')[0]
    h = {"X-BM-CSRF": csrf}
    assert c.get("/api/settings/alpaca").json() == {"configured": False}
    live = {"key": "AK" + "X" * 18, "secret": "s" * 40, "password": PW}
    assert c.post("/api/settings/alpaca", headers=h, data=live).status_code == 400
    paper = {"key": "PK" + "ABCDEFGH12345678WXYZ", "secret": "s" * 40, "password": PW}
    assert c.post("/api/settings/alpaca", headers=h, data={**paper, "password": "wrong"}).status_code == 403
    r = c.post("/api/settings/alpaca", headers=h, data=paper)
    assert r.status_code == 200 and r.json() == {"configured": True, "key_hint": "PK…WXYZ"}
    assert "s" * 40 not in c.get("/api/settings/alpaca").text
    store = SecretsStore(tmp_settings.data_dir)
    assert store.alpaca()[0].startswith("PK")
    import os, stat
    assert stat.S_IMODE(os.stat(store.dir / "alpaca.json").st_mode) == 0o600
