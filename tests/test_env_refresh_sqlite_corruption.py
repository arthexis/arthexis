import importlib.util
from pathlib import Path
from typing import List

from django.conf import settings
from django.db.utils import DatabaseError


def test_run_database_tasks_recovers_from_sqlite_corruption(monkeypatch):
    base_dir = Path(settings.BASE_DIR)
    spec = importlib.util.spec_from_file_location(
        "env_refresh_corruption", base_dir / "env-refresh.py"
    )
    env_refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_refresh)

    migrate_failures = {"raised": False}
    commands: List[str] = []

    def fake_call_command(name, *args, **kwargs):
        commands.append(name)
        if name == "migrate" and not migrate_failures["raised"]:
            migrate_failures["raised"] = True
            raise DatabaseError("database disk image is malformed")
        return None

    unlink_calls: List[Path] = []

    monkeypatch.setattr(env_refresh, "call_command", fake_call_command)
    monkeypatch.setattr(env_refresh, "_fixture_files", lambda: [])
    monkeypatch.setattr(env_refresh, "_unlink_sqlite_db", unlink_calls.append)

    env_refresh.run_database_tasks()

    assert migrate_failures["raised"] is True
    assert unlink_calls and unlink_calls[0].name == "test_db.sqlite3"
    assert commands.count("migrate") >= 2
