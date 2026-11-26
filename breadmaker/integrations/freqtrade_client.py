"""Minimal Freqtrade REST API client (private Docker network only)."""
from __future__ import annotations

import httpx


class FreqtradeClient:
    def __init__(self, base_url: str, username: str, password: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.auth = (username, password)
        self.http = httpx.Client(timeout=timeout)
        self._token: str | None = None

    def _headers(self) -> dict:
        if self._token is None:
            r = self.http.post(f"{self.base}/token/login", auth=self.auth)
            r.raise_for_status()
            self._token = r.json()["access_token"]
        return {"Authorization": f"Bearer {self._token}"}

    def _req(self, method: str, path: str, **kw):
        r = self.http.request(method, f"{self.base}/{path}", headers=self._headers(), **kw)
        if r.status_code == 401:  # token expired
            self._token = None
            r = self.http.request(method, f"{self.base}/{path}", headers=self._headers(), **kw)
        r.raise_for_status()
        return r.json()

    def ping(self) -> bool:
        try:
            return self.http.get(f"{self.base}/ping").json().get("status") == "pong"
        except httpx.HTTPError:
            return False

    def show_config(self) -> dict:
        return self._req("GET", "show_config")

    def balance(self) -> dict:
        return self._req("GET", "balance")

    def profit(self) -> dict:
        return self._req("GET", "profit")

    def status(self) -> list:
        return self._req("GET", "status")

    def trades(self, limit: int = 500, offset: int = 0) -> dict:
        return self._req("GET", "trades", params={"limit": limit, "offset": offset})

    def stopentry(self) -> dict:
        """Stop opening new trades; open trades continue to be managed (exits/stops)."""
        return self._req("POST", "stopentry")

    def forceexit_all(self) -> dict:
        return self._req("POST", "forceexit", json={"tradeid": "all"})
