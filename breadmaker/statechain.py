"""Fail-closed persistence of simulation state across GitHub Actions runs (artifact chain).

Audit of the previous approach (kalshi-paper.yml v0.3) found silent-reset paths:
  * it restored from the latest *successful run*; a run whose guard skipped the cycle is also
    "successful" but has no state artifact -> the next run started a fresh $25 book;
  * 14-day artifact retention -> any pause > 14 days wiped the history;
  * any download error fell through to "no previous state" -> reset;
  * restored databases were not integrity-checked.
Review of v0.4 (PR #3) found three more, fixed here:
  * seal() re-verified the previous manifest's file hashes AFTER the job had legitimately modified
    the files -> every second run of a chain would have failed with "hash mismatch";
  * "no artifact has ever existed" initialized silently - indistinguishable from GitHub having
    permanently deleted every previous artifact;
  * falling back to an older artifact (rollback) continued automatically with only a warning.

Lifecycle (one job):  restore -> (job modifies state) -> seal -> upload
  restore  verifies the downloaded MANIFEST against the downloaded files (hashes, SQLite
           integrity) and writes RESTORE_RECEIPT.json recording exactly which manifest it verified.
  seal     does NOT re-check old file hashes (the job changed the files on purpose). It checks that
           the manifest is byte-identical to the one restore verified, that no previously sealed
           file disappeared and no SQLite table lost rows (the ledgers are append-only), then writes
           generation = previous + 1, chained to the previous manifest's SHA-256.

Explicit owner authorization is required (exact phrases, via workflow_dispatch inputs) to:
  * start a new history when no artifact exists      (INIT_PHRASE, or RESET_PHRASE)
  * discard history that exists but is unusable       (RESET_PHRASE)
  * continue from an OLDER generation (rollback)      (ROLLBACK_PHRASE)
Everything else that is not a clean restore of the newest artifact raises StateLost (exit 2).
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
RECEIPT = "RESTORE_RECEIPT.json"
RESET_PHRASE = "I understand this permanently discards the simulated history"
INIT_PHRASE = "start a new simulated history"
ROLLBACK_PHRASE = "continue from the older state and lose the newer history"
_EXCLUDED = {MANIFEST, RECEIPT}


class StateLost(RuntimeError):
    """Previous state existed (or may have existed) but cannot be restored and verified. Never auto-reset."""


class RollbackRequired(StateLost):
    """The newest state is unusable; an older generation is restorable, but only with owner authorization."""


@dataclass
class RestoreResult:
    status: str            # 'restored' | 'rolled_back_by_owner' | 'initialized_by_owner' | 'reset_by_owner'
    generation: int | None  # generation restored; None when starting a new history
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


def _row_counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in sorted(tables)}
    finally:
        con.close()


def _state_files(state_dir: Path) -> list[Path]:
    return [p for p in sorted(state_dir.rglob("*"))
            if p.is_file() and p.name not in _EXCLUDED and not p.name.endswith(("-wal", "-shm", "-journal"))]


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


def read_receipt(state_dir: Path) -> dict | None:
    p = Path(state_dir) / RECEIPT
    return json.loads(p.read_text()) if p.exists() else None


def _write_receipt(state_dir: Path, status: str, generation: int | None, manifest_sha: str | None,
                   artifact_id: int | None, detail: str) -> None:
    (Path(state_dir) / RECEIPT).write_text(json.dumps({
        "status": status, "generation": generation, "manifest_sha256": manifest_sha, "artifact_id": artifact_id,
        "detail": detail, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2))


def seal(state_dir: Path, now: datetime | None = None) -> dict:
    """Write MANIFEST.json for the current (modified) state; generation = restored generation + 1.

    Requires the receipt written by restore() in this job, so a job that skipped or failed its
    restore can never upload a parallel 'generation 0' chain that would shadow the real history.
    """
    state_dir = Path(state_dir)
    rc = read_receipt(state_dir)
    if rc is None:
        raise StateLost("refusing to seal: no restore receipt (state must come from `statechain restore` in this job)")
    mp = state_dir / MANIFEST
    prev = None
    if rc.get("manifest_sha256"):
        if not mp.exists():
            raise StateLost("refusing to seal: the restored manifest has disappeared")
        raw = mp.read_bytes()
        if hashlib.sha256(raw).hexdigest() != rc["manifest_sha256"]:
            raise StateLost("refusing to seal: manifest changed after restore (does not match the restore receipt)")
        prev = json.loads(raw)
        if prev.get("generation") != rc.get("generation"):
            raise StateLost("refusing to seal: receipt and manifest disagree on the generation")
    elif mp.exists():
        raise StateLost("refusing to seal: a manifest exists but the receipt says this is a new history")

    files, tables = {}, {}
    for p in _state_files(state_dir):
        rel = str(p.relative_to(state_dir))
        if p.suffix == ".sqlite":
            if not _sqlite_ok(p):
                raise StateLost(f"refusing to seal: SQLite integrity check failed for {rel}")
            tables[rel] = _row_counts(p)
        files[rel] = _sha(p)
    if prev:
        gone = sorted(set(prev.get("files", {})) - set(files))
        if gone:
            raise StateLost(f"refusing to seal: file(s) from generation {prev['generation']} disappeared: {gone}")
        for rel, counts in (prev.get("tables") or {}).items():
            for t, n in counts.items():
                if tables.get(rel, {}).get(t, -1) < n:
                    raise StateLost(f"refusing to seal: {rel}:{t} lost rows ({n} -> {tables.get(rel, {}).get(t)}); "
                                    "the ledgers are append-only")
    m = {
        "schema": 2,
        "generation": (prev["generation"] + 1) if prev else 0,
        "sealed_at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        "files": files,
        "tables": tables,
        "previous_manifest_sha256": rc.get("manifest_sha256"),
        "origin": rc.get("status"),
        "summary": _summary(state_dir),
    }
    raw = json.dumps(m, indent=2, sort_keys=True).encode()
    mp.write_bytes(raw)
    # Point the receipt at the manifest just written, so a second seal in the same job chains correctly.
    _write_receipt(state_dir, "sealed", m["generation"], hashlib.sha256(raw).hexdigest(), rc.get("artifact_id"),
                   f"sealed generation {m['generation']}")
    return m


def verify(state_dir: Path) -> dict:
    """Verify a freshly DOWNLOADED state: manifest present, file hashes and SQLite integrity match."""
    state_dir = Path(state_dir)
    mp = state_dir / MANIFEST
    if not mp.exists():
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


def _clear(state_dir: Path) -> None:
    for p in sorted(state_dir.rglob("*"), reverse=True):
        if p.is_file() or p.is_symlink():
            p.unlink()
        elif p.is_dir():
            p.rmdir()


def restore(gh: GitHubArtifacts, name: str, state_dir: Path, reset_confirmation: str = "",
            init_confirmation: str = "", rollback_confirmation: str = "") -> RestoreResult:
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    if any(state_dir.iterdir()):
        raise StateLost(f"restore target {state_dir} is not empty; refusing to mix states")
    try:
        arts = gh.list_by_name(name)
    except httpx.HTTPError as exc:
        raise StateLost(f"cannot list artifacts ({exc}); refusing to guess whether state exists") from exc
    live = sorted([a for a in arts if not a.get("expired")], key=lambda a: a.get("created_at", ""), reverse=True)
    reset_ok = reset_confirmation.strip() == RESET_PHRASE
    init_ok = init_confirmation.strip() == INIT_PHRASE or reset_ok
    rollback_ok = rollback_confirmation.strip() == ROLLBACK_PHRASE

    if not arts:
        # Cannot tell "first run ever" from "GitHub permanently deleted every previous artifact".
        if init_ok:
            detail = "owner authorized a new history (no state artifact found)"
            _write_receipt(state_dir, "initialized_by_owner", None, None, None, detail)
            return RestoreResult("initialized_by_owner", None, None, detail)
        raise StateLost(f"no '{name}' artifact exists. This is either the very first run or GitHub has deleted "
                        "every previous state. Not starting over automatically: run the workflow manually with "
                        f"init_confirmation = '{INIT_PHRASE}' only if no history should exist.")
    errors = []
    for a in live[:3]:  # newest first; an older one is only ever restored with owner authorization
        try:
            zf = zipfile.ZipFile(io.BytesIO(gh.download_zip(a["id"])))
            for member in zf.namelist():
                if member.startswith("/") or ".." in Path(member).parts:
                    raise StateLost(f"unsafe path in artifact: {member}")
            zf.extractall(state_dir)
            (state_dir / RECEIPT).unlink(missing_ok=True)   # a stale receipt inside the artifact means nothing
            m = verify(state_dir)
        except (StateLost, zipfile.BadZipFile, httpx.HTTPError, OSError) as exc:
            errors.append(f"{a['id']}: {exc}")
            _clear(state_dir)
            continue
        where = f"artifact {a['id']} (generation {m['generation']}, created {a.get('created_at')})"
        if not errors:
            _write_receipt(state_dir, "restored", m["generation"], m["_sha256"], a["id"], f"restored {where}")
            return RestoreResult("restored", m["generation"], a["id"], f"restored {where}")
        if rollback_ok:
            detail = f"owner authorized ROLLBACK to {where}; newer artifacts unusable: {errors}"
            _write_receipt(state_dir, "rolled_back_by_owner", m["generation"], m["_sha256"], a["id"], detail)
            return RestoreResult("rolled_back_by_owner", m["generation"], a["id"], detail)
        _clear(state_dir)
        raise RollbackRequired(f"newest state unusable ({errors}); an older one is restorable: {where}. Not continuing "
                               f"automatically. Run manually with rollback_confirmation = '{ROLLBACK_PHRASE}' to accept "
                               "losing the newer history.")
    if reset_ok:
        detail = f"owner-confirmed reset; previous state unusable: {errors or 'all expired'}"
        _write_receipt(state_dir, "reset_by_owner", None, None, None, detail)
        return RestoreResult("reset_by_owner", None, None, detail)
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
        try:
            print(json.dumps(seal(Path(a.dir)), indent=2))
        except StateLost as exc:
            print(f"::error::STATE NOT SEALED - {exc}", file=sys.stderr)
            return 2
        return 0
    gh = GitHubArtifacts(os.environ["GITHUB_REPOSITORY"], os.environ["GH_TOKEN"])
    try:
        res = restore(gh, a.name, Path(a.dir), os.environ.get("STATE_RESET_CONFIRMATION", ""),
                      os.environ.get("STATE_INIT_CONFIRMATION", ""), os.environ.get("STATE_ROLLBACK_CONFIRMATION", ""))
    except StateLost as exc:
        print(f"::error::STATE LOST - {exc}", file=sys.stderr)
        return 2
    print(json.dumps(res.__dict__))
    if res.status != "restored":
        print(f"::warning::State chain: {res.status} - {res.detail}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
