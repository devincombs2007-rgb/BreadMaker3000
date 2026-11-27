"""State persistence must never silently reset the simulated bankroll or erase history.

A fake GitHub artifact store (in memory, behind httpx.MockTransport) stands in for the Actions
artifact API, so every test runs the real lifecycle: restore -> modify -> seal -> upload.
"""
import io
import json
import sqlite3
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import pytest

from breadmaker import forward
from breadmaker.statechain import (INIT_PHRASE, MANIFEST, RECEIPT, RESET_PHRASE, ROLLBACK_PHRASE, GitHubArtifacts,
                                   RollbackRequired, StateLost, main, read_receipt, restore, seal, verify)


class FakeGitHub:
    """Artifacts by name, like the Actions API: list (incl. expired), download zip, upload (upload-artifact)."""

    def __init__(self):
        self.arts: list[dict] = []
        self.zips: dict[int, bytes] = {}
        self.fail_download: set[int] = set()
        self.t0 = datetime(2026, 12, 1, tzinfo=timezone.utc)

    def upload(self, name: str, d: Path) -> int:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for p in sorted(d.rglob("*")):
                if p.is_file():
                    z.write(p, str(p.relative_to(d)))
        aid = len(self.arts) + 1
        self.arts.append({"id": aid, "name": name, "expired": False,
                          "created_at": (self.t0 + timedelta(days=aid)).isoformat()})
        self.zips[aid] = buf.getvalue()
        return aid

    def expire(self, aid):
        next(a for a in self.arts if a["id"] == aid)["expired"] = True

    def purge_all(self):          # GitHub permanently deletes artifacts
        self.arts.clear()
        self.zips.clear()

    def client(self) -> GitHubArtifacts:
        def handler(req):
            if req.url.path.endswith("/actions/artifacts"):
                name = req.url.params.get("name")
                return httpx.Response(200, json={"artifacts": [a for a in self.arts if a["name"] == name]})
            aid = int(req.url.path.split("/")[-2])
            if aid in self.zips and aid not in self.fail_download:
                return httpx.Response(200, content=self.zips[aid])
            return httpx.Response(500)
        return GitHubArtifacts("o/r", "t", transport=httpx.MockTransport(handler))


NAME = "kalshi-state"


def new_history(gh: FakeGitHub, d: Path, rows: int = 3) -> Path:
    restore(gh.client(), NAME, d, init_confirmation=INIT_PHRASE)
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("CREATE TABLE fills (id INTEGER)")
    con.execute("CREATE TABLE predictions (id INTEGER)")
    con.executemany("INSERT INTO fills VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()
    seal(d)
    return d


def add_fill(d: Path):
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("INSERT INTO fills VALUES ((SELECT COUNT(*) FROM fills))")
    con.commit()
    con.close()


# ---- the review finding: restore -> MODIFY -> seal must work --------------------------------------
def test_seal_after_modification_chains_generation(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "run0"))
    d = tmp_path / "run1"
    r = restore(gh.client(), NAME, d)
    assert r.status == "restored" and r.generation == 0
    prev_sha = read_receipt(d)["manifest_sha256"]
    add_fill(d)                                   # the job legitimately changes the database
    m = seal(d)                                   # v0.4 bug: raised "hash mismatch" here
    assert m["generation"] == 1 and m["previous_manifest_sha256"] == prev_sha
    assert m["tables"]["breadmaker.sqlite"]["fills"] == 4


def test_end_to_end_seven_cycles_with_real_forward_log(tmp_path):
    """Seven consecutive restore -> modify -> seal -> upload cycles (fresh runner dir each time),
    with an old artifact expiring part-way, using the real forward-log writer."""
    gh = FakeGitHub()
    d0 = tmp_path / "c0"
    restore(gh.client(), "forward-log-state", d0, init_confirmation=INIT_PHRASE)
    forward.connect(d0 / "forward.sqlite").close()
    seal(d0)
    gh.upload("forward-log-state", d0)
    resid = np.array([-2, -1, 0, 1, 2] * 10, float)
    chain = [json.loads((d0 / MANIFEST).read_text())]
    for i in range(1, 8):
        if i == 4:
            gh.expire(1)                          # old artifacts expiring is harmless
        d = tmp_path / f"c{i}"                    # every Actions run starts on an empty runner
        r = restore(gh.client(), "forward-log-state", d)
        assert r.status == "restored" and r.generation == i - 1
        con = forward.connect(d / "forward.sqlite")
        t = date(2026, 12, 1) + timedelta(days=i)
        assert forward.record_forecasts(con, "KNYC", t, 45.0, resid, datetime(2026, 12, i, tzinfo=timezone.utc)) == 9
        forward.settle(con, "KNYC", {t - timedelta(days=1): 46.0}, datetime(2026, 12, i, tzinfo=timezone.utc))
        con.close()
        m = seal(d)
        assert m["generation"] == i
        chain.append(m)
        gh.upload("forward-log-state", d)
    # the chain links every generation to its predecessor, and history only grows
    import hashlib
    for prev, cur in zip(chain, chain[1:]):
        prev_raw = json.dumps(prev, indent=2, sort_keys=True).encode()
        assert cur["previous_manifest_sha256"] == hashlib.sha256(prev_raw).hexdigest()
        assert cur["tables"]["forward.sqlite"]["fwd"] == prev["tables"]["forward.sqlite"]["fwd"] + 9
    final = tmp_path / "final"
    restore(gh.client(), "forward-log-state", final)
    con = forward.connect(final / "forward.sqlite")
    assert con.execute("SELECT COUNT(*) FROM fwd").fetchone()[0] == 63
    assert con.execute("SELECT COUNT(*) FROM fwd WHERE outcome IS NOT NULL").fetchone()[0] == 54


def test_cli_main_runs_five_cycles(tmp_path, monkeypatch):
    """The exact commands the workflows run (`restore`, `seal`), five times in a row."""
    gh = FakeGitHub()
    import breadmaker.statechain as sc
    monkeypatch.setattr(sc, "GitHubArtifacts", lambda repo, token: gh.client())
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")
    d = tmp_path / "first"
    assert main(["restore", "--name", NAME, "--dir", str(d)]) == 2          # no silent first-run init
    monkeypatch.setenv("STATE_INIT_CONFIRMATION", INIT_PHRASE)
    assert main(["restore", "--name", NAME, "--dir", str(d)]) == 0
    monkeypatch.delenv("STATE_INIT_CONFIRMATION")
    (d / "breadmaker.sqlite").touch()
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("CREATE TABLE fills (id INTEGER)")
    con.commit()
    con.close()
    assert main(["seal", "--dir", str(d)]) == 0
    gh.upload(NAME, d)
    for i in range(1, 6):
        d = tmp_path / f"run{i}"
        assert main(["restore", "--name", NAME, "--dir", str(d)]) == 0
        add_fill(d)
        assert main(["seal", "--dir", str(d)]) == 0
        gh.upload(NAME, d)
        assert json.loads((d / MANIFEST).read_text())["generation"] == i


# ---- tamper and erasure guards ---------------------------------------------------------------------
def test_tampered_artifact_detected_on_restore(tmp_path):
    gh = FakeGitHub()
    d = new_history(gh, tmp_path / "s")
    add_fill(d)                                   # modified after sealing, then uploaded as-is
    gh.upload(NAME, d)
    with pytest.raises(StateLost, match="hash mismatch"):
        restore(gh.client(), NAME, tmp_path / "out")


def test_seal_refuses_without_restore_receipt(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    sqlite3.connect(d / "breadmaker.sqlite").close()
    with pytest.raises(StateLost, match="no restore receipt"):
        seal(d)                                   # a job that skipped restore cannot start a parallel chain


def test_seal_refuses_if_manifest_swapped_after_restore(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    d = tmp_path / "r"
    restore(gh.client(), NAME, d)
    m = json.loads((d / MANIFEST).read_text())
    m["generation"] = 99
    (d / MANIFEST).write_text(json.dumps(m))
    with pytest.raises(StateLost, match="manifest changed"):
        seal(d)


def test_seal_refuses_lost_rows_or_files(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    d = tmp_path / "r"
    restore(gh.client(), NAME, d)
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("DELETE FROM fills")
    con.commit()
    con.close()
    with pytest.raises(StateLost, match="lost rows"):
        seal(d)
    d2 = tmp_path / "r2"
    restore(gh.client(), NAME, d2)
    (d2 / "breadmaker.sqlite").unlink()
    with pytest.raises(StateLost, match="disappeared"):
        seal(d2)


def test_restore_into_non_empty_dir_refused(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    d = tmp_path / "r"
    d.mkdir()
    (d / "leftover.sqlite").write_text("x")
    with pytest.raises(StateLost, match="not empty"):
        restore(gh.client(), NAME, d)


# ---- no silent initialization (incl. GitHub deleting every artifact) -------------------------------
def test_no_artifacts_requires_owner_init_phrase(tmp_path):
    gh = FakeGitHub()
    with pytest.raises(StateLost, match="very first run or GitHub has deleted"):
        restore(gh.client(), NAME, tmp_path / "a")
    with pytest.raises(StateLost):
        restore(gh.client(), NAME, tmp_path / "b", init_confirmation="yes")
    r = restore(gh.client(), NAME, tmp_path / "c", init_confirmation=INIT_PHRASE)
    assert r.status == "initialized_by_owner" and r.generation is None


def test_purge_after_history_does_not_silently_restart(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    for i in range(3):
        d = tmp_path / f"r{i}"
        restore(gh.client(), NAME, d)
        add_fill(d)
        seal(d)
        gh.upload(NAME, d)
    gh.purge_all()
    with pytest.raises(StateLost, match="deleted"):
        restore(gh.client(), NAME, tmp_path / "after")


def test_all_expired_fails_closed_and_reset_needs_exact_phrase(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    gh.expire(1)
    with pytest.raises(StateLost, match="Not resetting"):
        restore(gh.client(), NAME, tmp_path / "a")
    with pytest.raises(StateLost):
        restore(gh.client(), NAME, tmp_path / "b", init_confirmation=INIT_PHRASE)   # init phrase can't discard history
    with pytest.raises(StateLost):
        restore(gh.client(), NAME, tmp_path / "c", reset_confirmation="yes")
    r = restore(gh.client(), NAME, tmp_path / "d", reset_confirmation=RESET_PHRASE)
    assert r.status == "reset_by_owner"
    assert seal(tmp_path / "d")["generation"] == 0 and seal(tmp_path / "d")["generation"] == 1


def test_download_or_listing_failure_fails_closed(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    gh.fail_download.add(1)
    with pytest.raises(StateLost):
        restore(gh.client(), NAME, tmp_path / "a")
    broken = GitHubArtifacts("o/r", "t", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(StateLost, match="cannot list"):
        restore(broken, NAME, tmp_path / "b")


# ---- rollback needs explicit authorization ----------------------------------------------------------
def test_rollback_requires_owner_phrase(tmp_path):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))          # generation 0 (artifact 1)
    d = tmp_path / "r1"
    restore(gh.client(), NAME, d)
    add_fill(d)
    seal(d)
    gh.upload(NAME, d)                                         # generation 1 (artifact 2)
    gh.zips[2] = b"corrupted"
    with pytest.raises(RollbackRequired, match="generation 0"):
        restore(gh.client(), NAME, tmp_path / "a")
    assert not any((tmp_path / "a").iterdir())                 # nothing left behind to continue from
    with pytest.raises(RollbackRequired):
        restore(gh.client(), NAME, tmp_path / "b", reset_confirmation=RESET_PHRASE)   # wrong phrase
    r = restore(gh.client(), NAME, tmp_path / "c", rollback_confirmation=ROLLBACK_PHRASE)
    assert r.status == "rolled_back_by_owner" and r.generation == 0
    assert seal(tmp_path / "c")["origin"] == "rolled_back_by_owner"


def test_cli_rollback_exits_2(tmp_path, monkeypatch):
    gh = FakeGitHub()
    gh.upload(NAME, new_history(gh, tmp_path / "s"))
    gh.upload(NAME, tmp_path / "s")
    gh.zips[2] = b"bad"
    import breadmaker.statechain as sc
    monkeypatch.setattr(sc, "GitHubArtifacts", lambda repo, token: gh.client())
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GH_TOKEN", "t")
    assert main(["restore", "--name", NAME, "--dir", str(tmp_path / "x")]) == 2


# ---- misc ---------------------------------------------------------------------------------------------
def test_zip_slip_rejected(tmp_path):
    gh = FakeGitHub()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "x")
    gh.arts.append({"id": 1, "name": NAME, "expired": False, "created_at": "2026-01-01"})
    gh.zips[1] = buf.getvalue()
    with pytest.raises(StateLost):
        restore(gh.client(), NAME, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


def test_stale_receipt_inside_artifact_is_ignored(tmp_path):
    gh = FakeGitHub()
    d = new_history(gh, tmp_path / "s")
    assert (d / RECEIPT).exists()                              # it gets uploaded with the state
    gh.upload(NAME, d)
    out = tmp_path / "out"
    restore(gh.client(), NAME, out)
    assert read_receipt(out)["status"] == "restored"
    assert verify(out)["generation"] == 0
