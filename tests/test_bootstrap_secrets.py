"""Executes the real cloud-init bootstrap (and real deploy/tailscale.sh) with stub binaries and
fake credentials, then proves the credentials never reach logs, argv, or files left on disk."""
import os
import shutil
import stat
import subprocess
import textwrap

import pytest
import yaml

from conftest import ROOT

FAKE_TS = "tskey-auth-FAKEKEY1234567890abcdef"
FAKE_GH = "github_pat_FAKETOKEN_0123456789abcdefABCDEF"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or os.name != "posix", reason="needs bash")


def bootstrap_script() -> str:
    cfg = yaml.safe_load((ROOT / "deploy/cloud-init.yaml").read_text())
    return cfg["write_files"][0]["content"]


def test_no_shell_tracing_anywhere():
    sources = [(ROOT / f).read_text() for f in ["deploy/install.sh", "deploy/tailscale.sh"]] + [bootstrap_script()]
    for txt in sources:
        code = [line.split("#")[0] for line in txt.splitlines() if not line.strip().startswith("#")]
        assert not any("set -x" in c or "set -o xtrace" in c or "-euxo" in c for c in code)
        assert any(c.strip() == "set +x" for c in code)  # explicitly disables tracing


def _shim(dirpath, name, body):
    p = dirpath / name
    p.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body))
    p.chmod(0o755)


@pytest.mark.parametrize("traced", [False, True])
def test_bootstrap_never_leaks_credentials(tmp_path, traced):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    calls = tmp_path / "argv.log"
    seen = tmp_path / "seen.log"
    # Stubs record their argv (what `ps` would expose).
    for name in ["apt-get", "chown", "curl", "sudo-unused"]:
        _shim(bin_, name, f'echo "{name} $*" >> "{calls}"\n')
    _shim(bin_, "tee", f'echo "tee $*" >> "{calls}"; cat >/dev/null\n')
    # git: record argv + whether the auth header arrived via environment; build a fake checkout
    # containing the REAL deploy/tailscale.sh and a stub install.sh.
    _shim(bin_, "git", f'''
        echo "git $*" >> "{calls}"
        if [ "${{GIT_CONFIG_KEY_0:-}}" = "http.extraHeader" ]; then echo "GIT_HEADER=$GIT_CONFIG_VALUE_0" >> "{seen}"; fi
        dest="${{@: -1}}"; mkdir -p "$dest/.git" "$dest/deploy"
        cp "{ROOT}/deploy/tailscale.sh" "$dest/deploy/tailscale.sh"
        printf '#!/usr/bin/env bash\\necho install-stub-ran\\n' > "$dest/deploy/install.sh"
    ''')
    _shim(bin_, "tailscale", f'''
        echo "tailscale $*" >> "{calls}"
        for a in "$@"; do case "$a" in --auth-key=file:*) echo "TS_FILE_CONTENT=$(cat "${{a#--auth-key=file:}}")" >> "{seen}";; esac; done
        if [ "$1" = status ]; then echo '{{"Self":{{"DNSName":"breadmaker.example.ts.net."}}}}'; fi
    ''')
    script = bootstrap_script().replace("PASTE-TAILSCALE-AUTH-KEY-HERE", FAKE_TS).replace('GH_TOKEN=""', f'GH_TOKEN="{FAKE_GH}"', 1)
    self_path = tmp_path / "bootstrap.sh"
    self_path.write_text(script)
    secrets_parent = tmp_path / "run"
    secrets_parent.mkdir()
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "BM_DEST": str(tmp_path / "opt"),
           "BM_LOG": str(tmp_path / "bootstrap.log"), "BM_SELF": str(self_path),
           "BM_SECRETS_PARENT": str(secrets_parent)}
    cmd = ["bash", "-x", str(self_path)] if traced else ["bash", str(self_path)]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    log = (tmp_path / "bootstrap.log").read_text()
    assert "BREADMAKER BOOTSTRAP COMPLETE" in log

    # Credentials actually delivered through the safe channels...
    seen_txt = seen.read_text()
    assert f"TS_FILE_CONTENT={FAKE_TS}" in seen_txt
    assert "GIT_HEADER=Authorization: Basic " in seen_txt
    # ...but never in logs, process arguments, stdout/stderr, the script on disk, or leftover files.
    exposed = {
        "log": log, "argv": calls.read_text(), "stdout": r.stdout, "stderr": r.stderr,
        "script_after": self_path.read_text(),
    }
    for where, text in exposed.items():
        assert FAKE_TS not in text, f"tailscale key leaked in {where}"
        assert FAKE_GH not in text, f"github token leaked in {where}"
    assert list(secrets_parent.iterdir()) == [], "secret files not cleaned up"
    assert 'TS_AUTHKEY="removed-after-first-run"' in exposed["script_after"]


def test_cleanup_runs_even_on_failure(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    _shim(bin_, "apt-get", "exit 1\n")  # simulate an install failure
    script = bootstrap_script().replace("PASTE-TAILSCALE-AUTH-KEY-HERE", FAKE_TS)
    self_path = tmp_path / "bootstrap.sh"
    self_path.write_text(script)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "BM_DEST": str(tmp_path / "opt"),
           "BM_LOG": str(tmp_path / "b.log"), "BM_SELF": str(self_path), "BM_SECRETS_PARENT": str(run_dir)}
    r = subprocess.run(["bash", str(self_path)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode != 0
    assert list(run_dir.iterdir()) == []
    assert FAKE_TS not in self_path.read_text()
    assert stat.S_IMODE(self_path.stat().st_mode) is not None
