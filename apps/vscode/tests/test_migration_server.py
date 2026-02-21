"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
import subprocess
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




def test_stop_django_server_uses_direct_terminate_without_psutil() -> None:
    """Regression: fallback termination should run when psutil process lookup fails."""

    process = mock.Mock(spec=subprocess.Popen)
    process.poll.return_value = None
    process.pid = 999

    with mock.patch.object(migration_server, "_terminate_process_tree", return_value=False):
        migration_server.stop_django_server(process)

    process.terminate.assert_called_once_with()

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






def test_load_psutil_retries_after_transient_keyboard_interrupt() -> None:
    """Regression: psutil import should retry once after transient interrupt."""

    calls = {"count": 0}

    def fake_import_module(name: str):
        calls["count"] += 1
        if calls["count"] == 1:
            raise KeyboardInterrupt
        return object()

    with mock.patch.object(migration_server.importlib, "import_module", side_effect=fake_import_module):
        loaded = migration_server._load_psutil()

    assert loaded is not None
    assert calls["count"] == 2


def test_load_psutil_falls_back_when_module_is_missing() -> None:
    """Regression: migration server should handle missing psutil dependency."""

    with mock.patch.object(migration_server.importlib, "import_module", side_effect=ModuleNotFoundError):
        loaded = migration_server._load_psutil()

    assert isinstance(loaded, migration_server._PsutilUnavailable)

def test_windows_process_group_kwargs_uses_creation_flags() -> None:
    """Use a dedicated process group on Windows to avoid interrupt propagation."""

    with mock.patch.object(migration_server.os, "name", "nt"), mock.patch.object(
        migration_server.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, create=True
    ):
        assert migration_server._windows_process_group_kwargs() == {"creationflags": 512}


def test_windows_process_group_kwargs_is_empty_off_windows() -> None:
    """Return no special kwargs when not running on Windows."""

    with mock.patch.object(migration_server.os, "name", "posix"):
        assert migration_server._windows_process_group_kwargs() == {}

def test_run_env_refresh_prefers_sqlite_backend(tmp_path: Path) -> None:
    """Ensure migration-server refresh forces SQLite fallback for responsiveness."""

    script = tmp_path / "env-refresh.py"
    script.write_text("print('ok')", encoding="utf-8")

    completed = mock.Mock(returncode=0)
    with mock.patch.object(migration_server.subprocess, "run", return_value=completed) as mocked_run, mock.patch.object(
        migration_server, "_windows_process_group_kwargs", return_value={"creationflags": 512}
    ) as mocked_kwargs:
        assert migration_server.run_env_refresh(tmp_path, latest=True) is True

    mocked_kwargs.assert_called_once_with()
    _command, kwargs = mocked_run.call_args
    assert kwargs["creationflags"] == 512
    env = kwargs["env"]
    assert env["DJANGO_SETTINGS_MODULE"] == "config.settings"
    assert env["ARTHEXIS_DB_BACKEND"] == "sqlite"


def test_update_requirements_passes_windows_process_group_kwargs(tmp_path: Path) -> None:
    """Ensure dependency installation uses Windows process-group kwargs."""

    req_file = tmp_path / "requirements.txt"
    req_file.write_text("django==5.0\n", encoding="utf-8")

    hash_file = tmp_path / ".locks" / "requirements.sha256"
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text("different", encoding="utf-8")

    completed = mock.Mock(returncode=0)
    with mock.patch.object(migration_server, "REQUIREMENTS_FILE", Path("requirements.txt")), mock.patch.object(
        migration_server, "REQUIREMENTS_HASH_FILE", Path(".locks") / "requirements.sha256"
    ), mock.patch.object(
        migration_server.subprocess, "run", return_value=completed
    ) as mocked_run, mock.patch.object(
        migration_server, "_windows_process_group_kwargs", return_value={"creationflags": 512}
    ):
        assert migration_server.update_requirements(tmp_path) is True

    _command, kwargs = mocked_run.call_args
    assert kwargs["creationflags"] == 512
