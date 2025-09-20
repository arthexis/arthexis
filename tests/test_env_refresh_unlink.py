from pathlib import Path
from types import SimpleNamespace

from utils import env_refresh as env_refresh_utils


def test_unlink_sqlite_db_retries(monkeypatch, tmp_path):
    unlink_func = env_refresh_utils.unlink_sqlite_db
    path = tmp_path / "db.sqlite3"
    path.touch()

    calls = {"count": 0}
    original_unlink = Path.unlink

    def fake_unlink(self, missing_ok=False):
        if self == path and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    monkeypatch.setattr(
        env_refresh_utils, "connections", SimpleNamespace(close_all=lambda: None)
    )
    monkeypatch.setattr(
        env_refresh_utils, "time", SimpleNamespace(sleep=lambda s: None)
    )
    monkeypatch.setattr(
        env_refresh_utils, "settings", SimpleNamespace(BASE_DIR=tmp_path)
    )
    unlink_func(path)

    assert calls["count"] == 1
    assert not path.exists()
