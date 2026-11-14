import pytest

from epistemic_virtue.store import RUNS_DIR_ENV


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    """Keep every test's saved runs out of the repository's runs/ directory."""
    path = tmp_path / "runs"
    monkeypatch.setenv(RUNS_DIR_ENV, str(path))
    return path
