"""Owner secrets kept on the server's private data volume (never in Git, never logged).

Lets an iPhone-only owner finish setup in the dashboard instead of a terminal:
* dashboard password hash (set once on first visit; reset requires server access),
* session signing key (auto-generated),
* Alpaca PAPER keys (validated to be paper keys; never returned by any API).
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .config import SafetyError, assert_alpaca_key_looks_paper


class SecretsStore:
    def __init__(self, data_dir: str | Path):
        self.dir = Path(data_dir) / "secrets"

    def _ensure(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)

    def _write_new(self, name: str, content: str) -> bool:
        """Atomic create-only write (O_EXCL). Returns False if it already exists."""
        self._ensure()
        try:
            fd = os.open(self.dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        return True

    def _replace(self, name: str, content: str) -> None:
        self._ensure()
        tmp = self.dir / f".{name}.tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(content)
        tmp.replace(self.dir / name)

    def _read(self, name: str) -> str | None:
        try:
            return (self.dir / name).read_text().strip()
        except FileNotFoundError:
            return None

    # ---- dashboard -------------------------------------------------------
    def dashboard_hash(self) -> str | None:
        return os.environ.get("DASHBOARD_PASSWORD_HASH") or self._read("dashboard_password_hash")

    def set_dashboard_hash_once(self, h: str) -> bool:
        if self.dashboard_hash():
            return False
        return self._write_new("dashboard_password_hash", h)

    def session_key(self) -> str:
        env = os.environ.get("DASHBOARD_SECRET_KEY", "")
        if len(env) >= 32:
            return env
        existing = self._read("session_key")
        if existing:
            return existing
        self._write_new("session_key", secrets.token_hex(32))
        return self._read("session_key") or ""

    # ---- alpaca ------------------------------------------------------------
    def save_alpaca(self, key: str, secret: str) -> None:
        key, secret = key.strip(), secret.strip()
        assert_alpaca_key_looks_paper(key)
        if not key.upper().startswith("PK") or len(key) < 16 or len(secret) < 20:
            raise SafetyError("Alpaca PAPER key IDs start with 'PK'; check both values were copied fully.")
        self._replace("alpaca.json", json.dumps({"key": key, "secret": secret}))

    def alpaca(self) -> tuple[str, str] | None:
        if os.environ.get("ALPACA_API_KEY") and os.environ.get("ALPACA_API_SECRET"):
            return os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]
        raw = self._read("alpaca.json")
        if not raw:
            return None
        d = json.loads(raw)
        return d["key"], d["secret"]

    def alpaca_status(self) -> dict:
        creds = self.alpaca()
        if not creds:
            return {"configured": False}
        return {"configured": True, "key_hint": f"{creds[0][:2]}…{creds[0][-4:]}"}
