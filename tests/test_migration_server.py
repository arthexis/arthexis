from __future__ import annotations

import json
import os
import sys
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


def test_main_runs_env_refresh_immediately(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[Path, bool]] = []
    restarts: list[Path] = []
    updates: list[Path] = []

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
    monkeypatch.setattr(
        migration_server, "request_runserver_restart", lambda lock_dir: restarts.append(lock_dir)
    )
    monkeypatch.setattr(
        migration_server,
        "update_requirements",
        lambda base_dir: updates.append(base_dir) or False,
    )
    monkeypatch.setattr(migration_server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(migration_server, "LOCK_DIR", tmp_path / "locks")

    result = migration_server.main([])

    assert result == 0
    assert len(calls) == 1
    assert restarts == [tmp_path / "locks"]
    assert updates == [tmp_path]


def test_main_notifies_and_stops_on_new_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[Path, bool]] = []
    notifications: list[tuple[str, str]] = []
    update_calls: list[Path] = []

    def fake_collect(_: Path) -> dict[str, int]:
        return {}

    def fake_wait_for_changes(_: Path, __: dict[str, int], **___: object) -> dict[str, int]:
        return {"requirements.txt": 1}

    def fake_run_env_refresh(_: Path, *, latest: bool) -> bool:
        calls.append((migration_server.BASE_DIR, latest))
        return True

    def fake_update(base_dir: Path) -> bool:
        update_calls.append(base_dir)
        return len(update_calls) > 1

    monkeypatch.setattr(migration_server, "collect_source_mtimes", fake_collect)
    monkeypatch.setattr(migration_server, "wait_for_changes", fake_wait_for_changes)
    monkeypatch.setattr(migration_server, "run_env_refresh", fake_run_env_refresh)
    monkeypatch.setattr(migration_server, "update_requirements", fake_update)
    monkeypatch.setattr(
        migration_server,
        "notify_async",
        lambda subject, body="": notifications.append((subject, body)),
    )
    monkeypatch.setattr(migration_server, "BASE_DIR", tmp_path)
    monkeypatch.setattr(migration_server, "LOCK_DIR", tmp_path / "locks")

    result = migration_server.main(["--debounce", "0"])

    assert result == 0
    assert len(calls) == 1
    assert notifications == [
        (
            "New Python requirements installed",
            "The migration server stopped after installing new dependencies.",
        )
    ]
    assert update_calls == [tmp_path, tmp_path]


def test_migration_server_state_records_pid(tmp_path: Path) -> None:
    with migration_server.migration_server_state(tmp_path / "locks") as state_path:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert data["pid"] == os.getpid()
        assert state_path.exists()
    assert not state_path.exists()


def test_request_runserver_restart_creates_signal(tmp_path: Path) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    token = "abc123"
    state_path = lock_dir / "vscode_runserver.json"
    state_path.write_text(
        json.dumps({"pid": os.getpid(), "token": token}), encoding="utf-8"
    )

    migration_server.request_runserver_restart(lock_dir)

    restart_path = lock_dir / f"vscode_runserver.restart.{token}"
    assert restart_path.exists()


def test_request_runserver_restart_ignores_dead_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    token = "dead"
    state_path = lock_dir / "vscode_runserver.json"
    state_path.write_text(json.dumps({"pid": 999999, "token": token}), encoding="utf-8")

    monkeypatch.setattr(migration_server, "_is_process_alive", lambda pid: False)

    migration_server.request_runserver_restart(lock_dir)

    assert not any(lock_dir.glob("vscode_runserver.restart.*"))


def test_update_requirements_installs_when_hash_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("demo==1.0\n", encoding="utf-8")
    helper = tmp_path / "scripts" / "helpers"
    helper.mkdir(parents=True)
    (helper / "pip_install.py").write_text("print('noop')\n", encoding="utf-8")

    runs: list[tuple[list[str], Path | None]] = []

    class DummyResult:
        returncode = 0

    def fake_run(cmd, cwd=None, **_: object):
        runs.append((cmd, cwd))
        return DummyResult()

    monkeypatch.setattr(migration_server.subprocess, "run", fake_run)

    updated = migration_server.update_requirements(tmp_path)

    assert updated is True
    assert runs
    assert runs[0][0][0] == sys.executable
    assert (tmp_path / "requirements.md5").exists()

    runs.clear()
    updated_again = migration_server.update_requirements(tmp_path)
    assert updated_again is False
    assert not runs


def test_update_requirements_reports_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("demo==1.0\n", encoding="utf-8")
    notifications: list[tuple[str, str]] = []

    class DummyResult:
        returncode = 1

    monkeypatch.setattr(
        migration_server.subprocess,
        "run",
        lambda *args, **kwargs: DummyResult(),
    )
    monkeypatch.setattr(
        migration_server,
        "notify_async",
        lambda subject, body="": notifications.append((subject, body)),
    )

    updated = migration_server.update_requirements(tmp_path)

    assert updated is False
    assert notifications == [
        (
            "Python requirements update failed",
            "See migration server output for details.",
        )
    ]
    assert not (tmp_path / "requirements.md5").exists()
