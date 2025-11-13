from __future__ import annotations

import time
from pathlib import Path

import pytest

from scripts import migration_server


def test_should_watch_file_filters_extensions(tmp_path: Path) -> None:
    watched = migration_server._should_watch_file(Path("core/models.py"))
    skipped = migration_server._should_watch_file(Path("logs/output.log"))
    assert watched is True
    assert skipped is False


def test_collect_source_mtimes_skips_excluded_directories(tmp_path: Path) -> None:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    watched_file = app_dir / "models.py"
    watched_file.write_text("print('hi')", encoding="utf-8")

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "app.log").write_text("ignored", encoding="utf-8")

    snapshot = migration_server.collect_source_mtimes(tmp_path)
    assert "app/models.py" in snapshot
    assert all(not key.startswith("logs/") for key in snapshot)


def test_diff_snapshots_reports_added_removed_and_modified() -> None:
    previous = {"core/models.py": 1, "core/views.py": 2}
    current = {"core/models.py": 1, "core/forms.py": 3, "core/views.py": 4}
    diff = migration_server.diff_snapshots(previous, current)
    assert "added core/forms.py" in diff
    assert "removed core/models.py" not in diff
    assert "modified core/views.py" in diff


def test_build_env_refresh_command_uses_latest(tmp_path: Path) -> None:
    script = tmp_path / "env-refresh.py"
    script.write_text("print('ready')", encoding="utf-8")
    command = migration_server.build_env_refresh_command(tmp_path, latest=True)
    assert command[-2:] == ["--latest", "database"]
    command_no_latest = migration_server.build_env_refresh_command(tmp_path, latest=False)
    assert command_no_latest[-1] == "database"
    assert "--latest" not in command_no_latest


@pytest.mark.timeout(5)
def test_wait_for_changes_detects_file_updates(tmp_path: Path) -> None:
    target = tmp_path / "env-refresh.py"
    target.write_text("print('ready')", encoding="utf-8")
    snapshot = migration_server.collect_source_mtimes(tmp_path)

    def update_file() -> None:
        time.sleep(0.2)
        target.write_text("print('changed')", encoding="utf-8")

    update_file()
    updated = migration_server.wait_for_changes(tmp_path, snapshot, interval=0.1)
    assert updated != snapshot


def test_main_runs_env_refresh_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, bool]] = []

    def fake_collect(_: Path) -> dict[str, int]:
        return {}

    def fake_wait_for_changes(_: Path, __: dict[str, int], **___: object) -> dict[str, int]:
        raise KeyboardInterrupt

    def fake_run_env_refresh(_: Path, *, latest: bool) -> bool:
        calls.append((migration_server.BASE_DIR, latest))
        return True

    monkeypatch.setattr(migration_server, "collect_source_mtimes", fake_collect)
    monkeypatch.setattr(migration_server, "wait_for_changes", fake_wait_for_changes)
    monkeypatch.setattr(migration_server, "run_env_refresh", fake_run_env_refresh)

    result = migration_server.main([])

    assert result == 0
    assert len(calls) == 1
