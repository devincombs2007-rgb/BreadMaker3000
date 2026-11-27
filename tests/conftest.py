import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "engines" / "lumibot"))


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BREADMAKER_DATA_DIR", str(tmp_path))
    from breadmaker.config import load_settings
    return load_settings()


@pytest.fixture
def conn(tmp_path):
    from breadmaker.db import connect
    c = connect(tmp_path / "t.sqlite")
    yield c
    c.close()
