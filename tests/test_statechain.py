"""State persistence must never silently reset the simulated bankroll or erase history."""
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import httpx
import pytest

from breadmaker.statechain import RESET_PHRASE, GitHubArtifacts, StateLost, restore, seal, verify


def make_state(d: Path, rows: int = 3) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("CREATE TABLE fills (id INTEGER)")
    con.execute("CREATE TABLE predictions (id INTEGER)")
    con.executemany("INSERT INTO fills VALUES (?)", [(i,) for i in range(rows)])
    con.commit()
    con.close()
    return d


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in d.iterdir():
            z.write(p, p.name)
    return buf.getvalue()


def gh_with(artifacts, zips):
    def handler(req):
        if req.url.path.endswith("/actions/artifacts"):
            return httpx.Response(200, json={"artifacts": artifacts})
        aid = int(req.url.path.split("/")[-2])
        if aid in zips:
            return httpx.Response(200, content=zips[aid])
        return httpx.Response(500)
    return GitHubArtifacts("o/r", "t", transport=httpx.MockTransport(handler))


def test_seal_and_verify_generations(tmp_path):
    d = make_state(tmp_path / "s")
    m0 = seal(d)
    assert m0["generation"] == 0 and m0["summary"]["fills"] == 3
    m1 = seal(d)
    assert m1["generation"] == 1 and m1["previous_manifest_sha256"]
    assert verify(d)["generation"] == 1


def test_tamper_detected(tmp_path):
    d = make_state(tmp_path / "s")
    seal(d)
    con = sqlite3.connect(d / "breadmaker.sqlite")
    con.execute("INSERT INTO fills VALUES (99)")
    con.commit()
    con.close()
    with pytest.raises(StateLost, match="hash mismatch"):
        verify(d)


def test_first_run_initializes(tmp_path):
    r = restore(gh_with([], {}), "kalshi-state", tmp_path / "out")
    assert r.status == "initialized" and r.generation == 0


def test_restores_newest_live_artifact(tmp_path):
    d = make_state(tmp_path / "s")
    seal(d)
    seal(d)
    arts = [{"id": 1, "expired": False, "created_at": "2026-01-01"}, {"id": 2, "expired": False, "created_at": "2026-01-02"}]
    r = restore(gh_with(arts, {2: zip_dir(d), 1: b"junk"}), "kalshi-state", tmp_path / "out")
    assert r.status == "restored" and r.artifact_id == 2 and r.generation == 1


def test_skipped_runs_do_not_reset(tmp_path):
    """Old bug: a guard-only 'successful' run had no artifact -> fresh book. Lookup by name avoids it."""
    d = make_state(tmp_path / "s")
    seal(d)
    arts = [{"id": 7, "expired": False, "created_at": "2026-01-01"}]
    r = restore(gh_with(arts, {7: zip_dir(d)}), "kalshi-state", tmp_path / "out")
    assert r.status == "restored" and json.loads((tmp_path / "out" / "MANIFEST.json").read_text())["summary"]["fills"] == 3


def test_all_expired_fails_closed(tmp_path):
    arts = [{"id": 1, "expired": True, "created_at": "2026-01-01"}]
    with pytest.raises(StateLost, match="Not resetting"):
        restore(gh_with(arts, {}), "kalshi-state", tmp_path / "out")


def test_download_failure_fails_closed(tmp_path):
    arts = [{"id": 5, "expired": False, "created_at": "2026-01-01"}]
    with pytest.raises(StateLost):
        restore(gh_with(arts, {}), "kalshi-state", tmp_path / "out")   # server 500


def test_corrupt_artifact_fails_closed_and_listing_error_fails_closed(tmp_path):
    arts = [{"id": 5, "expired": False, "created_at": "2026-01-01"}]
    with pytest.raises(StateLost):
        restore(gh_with(arts, {5: b"not a zip"}), "kalshi-state", tmp_path / "out")
    broken = GitHubArtifacts("o/r", "t", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(StateLost, match="cannot list"):
        restore(broken, "kalshi-state", tmp_path / "out2")


def test_fallback_to_older_is_flagged_as_rollback(tmp_path):
    d = make_state(tmp_path / "s")
    seal(d)
    arts = [{"id": 1, "expired": False, "created_at": "2026-01-01"}, {"id": 2, "expired": False, "created_at": "2026-01-02"}]
    r = restore(gh_with(arts, {1: zip_dir(d), 2: b"corrupt"}), "kalshi-state", tmp_path / "out")
    assert r.status == "restored_fallback" and r.artifact_id == 1


def test_reset_only_with_exact_owner_phrase(tmp_path):
    arts = [{"id": 1, "expired": True}]
    with pytest.raises(StateLost):
        restore(gh_with(arts, {}), "x", tmp_path / "a", reset_confirmation="yes")
    r = restore(gh_with(arts, {}), "x", tmp_path / "b", reset_confirmation=RESET_PHRASE)
    assert r.status == "reset_by_owner"


def test_zip_slip_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.txt", "x")
    arts = [{"id": 3, "expired": False, "created_at": "2026-01-01"}]
    with pytest.raises(StateLost):
        restore(gh_with(arts, {3: buf.getvalue()}), "x", tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()
