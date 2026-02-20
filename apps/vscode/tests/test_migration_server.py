"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from unittest import mock

from apps.vscode import migration_server


def test_stop_django_server_terminates_multiprocessing_process() -> None:
    """Ensure stop_django_server handles multiprocessing-based servers."""

    process = mock.Mock(spec=multiprocessing.Process)
    process.is_alive.return_value = True
    process.pid = 4321

    child = mock.Mock()
    parent = mock.Mock()
    parent.children.return_value = [child]

    wait_procs = mock.Mock(return_value=([], []))

    with mock.patch.object(migration_server.psutil, "Process", return_value=parent), \
        mock.patch.object(migration_server.psutil, "wait_procs", wait_procs):
        migration_server.stop_django_server(process)

    parent.children.assert_called_once_with(recursive=True)
    child.terminate.assert_called_once()
    parent.terminate.assert_called_once()
    wait_procs.assert_any_call([child, parent], timeout=5.0)
    process.join.assert_called_once_with(timeout=0.1)


def test_resolve_base_dir_uses_script_location(tmp_path) -> None:
    """Resolve the base directory from the migration server location."""

    resolved = migration_server.resolve_base_dir(
        env={"VSCODE_WORKSPACE_FOLDER": str(tmp_path)},
        cwd=tmp_path,
    )

    expected = Path(migration_server.__file__).resolve().parents[2]
    assert resolved == expected


def test_notify_async_is_a_noop() -> None:
    """Notifications are intentionally disabled for migration-server workflows."""

    with mock.patch("builtins.print") as mocked_print:
        migration_server.notify_async("Subject", "Body")

    mocked_print.assert_not_called()


def test_run_env_refresh_prefers_sqlite_backend(tmp_path: Path) -> None:
    """Ensure migration-server refresh forces SQLite fallback for responsiveness."""

    script = tmp_path / "env-refresh.py"
    script.write_text("print('ok')", encoding="utf-8")

    completed = mock.Mock(returncode=0)
    with mock.patch.object(migration_server.subprocess, "run", return_value=completed) as mocked_run:
        assert migration_server.run_env_refresh(tmp_path, latest=True) is True

    _command, kwargs = mocked_run.call_args
    env = kwargs["env"]
    assert env["DJANGO_SETTINGS_MODULE"] == "config.settings"
    assert env["ARTHEXIS_DB_BACKEND"] == "sqlite"
