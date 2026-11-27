"""Fail-closed persistence of simulation state across GitHub Actions runs (artifact chain).

Audit of the previous approach (kalshi-paper.yml v0.3) found silent-reset paths:
  * it restored from the latest *successful run*; a run whose guard skipped the cycle is also
    "successful" but has no state artifact -> the next run started a fresh $25 book;
  * 14-day artifact retention -> any pause > 14 days wiped the history;
  * any download error fell through to "no previous state" -> reset;
  * restored databases were not integrity-checked.

Rules implemented here:
  1. Look up artifacts BY NAME across all runs (GitHub lists expired ones with expired=true).
  2. Newest non-expired artifact -> download, then verify MANIFEST (file hashes, SQLite
     integrity_check, generation number). Any failure -> StateLost (job fails loudly).
  3. No artifact has EVER existed -> first run: start fresh (generation 0).
  4. Artifacts existed but all expired / unusable -> StateLost unless the owner explicitly
     passes the reset confirmation phrase.
  5. seal() increments the generation and chains the previous manifest hash.
"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

MANIFEST = "MANIFEST.json"
RESET_PHRASE = "I understand this permanently discards the simulated history"


class StateLost(RuntimeError):
    """Previous state existed but cannot be restored and verified. Never auto-reset."""


@dataclass
class RestoreResult:
    status: str            # 'restored' | 'restored_fallback' (older state: ROLLBACK) | 'initialized' | 'reset_by_owner'
    generation: int
    artifact_id: int | None
    detail: str


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sqlite_ok(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        con.close()
        return ok
    except sqlite3.Error:
        return False


def _summary(state_dir: Path) -> dict:
    out = {}
    db = state_dir / "breadmaker.sqlite"
    if db.exists():
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            out["fills"] = con.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
            out["predictions"] = con.execute("SELECT COUNT(*) FROM predictions").fetchone()[0]
        except sqlite3.Error:
            pass
        con.close()
    return out


def seal(state_dir: Path, now: datetime | None = None) -> dict:
    """Write MANIFEST.json for the current state; generation = previous + 1."""
    state_dir = Path(state_dir)
    prev = verify(state_dir, allow_missing_manifest=True)
    files = {}
    for p in sorted(state_dir.rglob("*")):
        if p.is_file() and p.name != MANIFEST and not p.name.endswith(("-wal", "-shm")):
            if p.suffix == ".sqlite" and not _sqlite_ok(p):
                raise StateLost(f"refusing to seal: SQLite integrity check failed for {p.name}")
            files[str(p.relative_to(state_dir))] = _sha(p)
    m = {
        "schema": 1,
        "generation": (prev["generation"] + 1) if prev else 0,
        "sealed_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "files": files,
        "previous_manifest_sha256": prev.get("_sha256") if prev else None,
        "summary": _summary(state_dir),
    }
    (state_dir / MANIFEST).write_text(json.dumps(m, indent=2, sort_keys=True))
    return m


def verify(state_dir: Path, allow_missing_manifest: bool = False) -> dict | None:
    state_dir = Path(state_dir)
    mp = state_dir / MANIFEST
    if not mp.exists():
        if allow_missing_manifest:
            return None
        raise StateLost("manifest missing")
    raw = mp.read_bytes()
    try:
        m = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StateLost(f"manifest unreadable: {exc}") from exc
    if not isinstance(m.get("generation"), int) or m["generation"] < 0:
        raise StateLost("manifest generation invalid")
    for rel, digest in (m.get("files") or {}).items():
        p = state_dir / rel
        if not p.exists():
            raise StateLost(f"file listed in manifest is missing: {rel}")
        if _sha(p) != digest:
            raise StateLost(f"hash mismatch for {rel}")
        if p.suffix == ".sqlite" and not _sqlite_ok(p):
            raise StateLost(f"SQLite integrity check failed for {rel}")
    m["_sha256"] = hashlib.sha256(raw).hexdigest()
    return m


class GitHubArtifacts:
    def __init__(self, repo: str, token: str, api: str = "https://api.github.com",
                 transport: httpx.BaseTransport | None = None):
        self.repo = repo
        self.http = httpx.Client(base_url=api, transport=transport, timeout=60, follow_redirects=True,
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28"})

    def list_by_name(self, name: str) -> list[dict]:
        out, page = [], 1
        while True:
            r = self.http.get(f"/repos/{self.repo}/actions/artifacts", params={"name": name, "per_page": 100, "page": page})
            r.raise_for_status()
            items = r.json().get("artifacts", [])
            out.extend(items)
            if len(items) < 100:
                return out
            page += 1

    def download_zip(self, artifact_id: int) -> bytes:
        r = self.http.get(f"/repos/{self.repo}/actions/artifacts/{artifact_id}/zip")
        r.raise_for_status()
        return r.content


def restore(gh: GitHubArtifacts, name: str, state_dir: Path, reset_confirmation: str = "") -> RestoreResult:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        arts = gh.list_by_name(name)
    except httpx.HTTPError as exc:
        raise StateLost(f"cannot list artifacts ({exc}); refusing to guess whether state exists") from exc
    live = sorted([a for a in arts if not a.get("expired")], key=lambda a: a.get("created_at", ""), reverse=True)
    reset_ok = reset_confirmation.strip() == RESET_PHRASE

    if not arts:
        return RestoreResult("initialized", 0, None, "no state artifact has ever existed: first run")
    errors = []
    for a in live[:3]:  # newest first; fall back to the next newest only if the newest is unusable
        try:
            zf = zipfile.ZipFile(io.BytesIO(gh.download_zip(a["id"])))
            for member in zf.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise StateLost(f"unsafe path in artifact: {member}")
            zf.extractall(state_dir)
            m = verify(state_dir)
            return RestoreResult("restored_fallback" if errors else "restored", m["generation"], a["id"],
                                 f"restored artifact {a['id']} created {a.get('created_at')}"
                                 + (f" (newer artifacts unusable: {errors})" if errors else ""))
        except (StateLost, zipfile.BadZipFile, httpx.HTTPError) as exc:
            errors.append(f"{a['id']}: {exc}")
            for p in state_dir.iterdir():
                if p.is_file():
                    p.unlink()
    if reset_ok:
        return RestoreResult("reset_by_owner", 0, None, f"owner-confirmed reset; previous state unusable: {errors or 'all expired'}")
    raise StateLost(f"state existed ({len(arts)} artifact(s), {len(live)} unexpired) but none could be restored: "
                    f"{errors or 'all expired'}. Not resetting. Re-run with the reset phrase only if intended.")


def main(argv=None) -> int:
    import argparse
    import os
    import sys
    ap = argparse.ArgumentParser(prog="breadmaker state")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--name", required=True)
    r.add_argument("--dir", required=True)
    s = sub.add_parser("seal")
    s.add_argument("--dir", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "seal":
        print(json.dumps(seal(Path(a.dir)), indent=2))
        return 0
    gh = GitHubArtifacts(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    try:
        res = restore(gh, a.name, Path(a.dir), os.environ.get("STATE_RESET_CONFIRMATION", ""))
    except StateLost as exc:
        print(f"::error::STATE LOST - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res.__dict__))
    if res.status == "restored_fallback":
        print(f"::warning::State ROLLED BACK to an older generation: {res.detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
