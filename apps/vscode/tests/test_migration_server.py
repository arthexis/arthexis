"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
import subprocess
from pathlib import Path
from unittest import mock

import pytest

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

    fake_psutil = mock.Mock()
    fake_psutil.Process.return_value = parent
    fake_psutil.wait_procs = wait_procs

    with mock.patch.object(migration_server, "_load_psutil", return_value=fake_psutil):
        migration_server.stop_django_server(process)

    parent.children.assert_called_once_with(recursive=True)
    child.terminate.assert_called_once()
    parent.terminate.assert_called_once()
    wait_procs.assert_any_call([child, parent], timeout=5.0)
    process.join.assert_called_once_with(timeout=0.1)




def test_stop_django_server_falls_back_without_psutil() -> None:
    """Regression: stop_django_server should terminate without psutil installed."""

    process = mock.Mock(spec=subprocess.Popen)
    process.poll.return_value = None
    process.pid = 9876

    with mock.patch.object(migration_server, "_load_psutil", return_value=None), mock.patch.object(
        migration_server, "_terminate_process_without_psutil"
    ) as fallback:
        migration_server.stop_django_server(process)

    fallback.assert_called_once_with(9876)

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


def test_read_migration_server_state_cleans_stale_lock(tmp_path: Path) -> None:
    """Regression: stale migration lock files should be removed automatically."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / migration_server.MIGRATION_SERVER_LOCK_FILE
    lock_path.write_text('{"pid": 12345, "status": "processing"}', encoding="utf-8")

    with mock.patch.object(migration_server, "_is_process_alive", return_value=False):
        state = migration_server.read_migration_server_state(lock_dir)

    assert state is None
    assert lock_path.exists() is False


def test_read_migration_server_state_normalizes_status(tmp_path: Path) -> None:
    """Regression: unknown migration statuses should be treated as idle."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)
    lock_path = lock_dir / migration_server.MIGRATION_SERVER_LOCK_FILE
    lock_path.write_text('{"pid": 12345, "status": "unknown"}', encoding="utf-8")

    with mock.patch.object(migration_server, "_is_process_alive", return_value=True):
        state = migration_server.read_migration_server_state(lock_dir)

    assert state is not None
    assert state["pid"] == 12345
    assert state["status"] == migration_server.MIGRATION_STATUS_IDLE


def test_update_migration_server_status_updates_existing_lock(tmp_path: Path) -> None:
    """Ensure migration status transitions rewrite the existing lock payload."""

    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir(parents=True)

    migration_server.write_migration_server_state(
        lock_dir,
        pid=321,
        status=migration_server.MIGRATION_STATUS_IDLE,
    )

    with mock.patch.object(migration_server, "_is_process_alive", return_value=True):
        migration_server.update_migration_server_status(
            lock_dir,
            migration_server.MIGRATION_STATUS_PROCESSING,
        )
        updated = migration_server.read_migration_server_state(lock_dir)

    assert updated is not None
    assert updated["pid"] == 321
    assert updated["status"] == migration_server.MIGRATION_STATUS_PROCESSING






def test_run_refresh_with_status_leaves_processing_on_failure(monkeypatch):
    """Regression: failed refresh should not advertise an idle migration state."""

    status_updates: list[str] = []

    monkeypatch.setattr(
        migration_server,
        "update_migration_server_status",
        lambda _lock_dir, status: status_updates.append(status),
    )
    monkeypatch.setattr(
        migration_server,
        "run_env_refresh_with_report",
        lambda _base_dir, *, latest: False,
    )

    assert migration_server._run_refresh_with_status(Path('.'), latest=True) is False
    assert status_updates == [migration_server.MIGRATION_STATUS_PROCESSING]


def test_run_refresh_with_status_sets_idle_on_success(monkeypatch):
    """Regression: successful refresh should return the lock state to idle."""

    status_updates: list[str] = []

    monkeypatch.setattr(
        migration_server,
        "update_migration_server_status",
        lambda _lock_dir, status: status_updates.append(status),
    )
    monkeypatch.setattr(
        migration_server,
        "run_env_refresh_with_report",
        lambda _base_dir, *, latest: True,
    )

    assert migration_server._run_refresh_with_status(Path('.'), latest=False) is True
    assert status_updates == [
        migration_server.MIGRATION_STATUS_PROCESSING,
        migration_server.MIGRATION_STATUS_IDLE,
    ]

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


def test_posix_process_group_kwargs_uses_new_session() -> None:
    """Use a dedicated process group on POSIX for whole-tree termination."""

    with mock.patch.object(migration_server.os, "name", "posix"):
        assert migration_server._posix_process_group_kwargs() == {"start_new_session": True}


def test_posix_process_group_kwargs_is_empty_off_posix() -> None:
    """Return no special kwargs when not running on POSIX."""

    with mock.patch.object(migration_server.os, "name", "nt"):
        assert migration_server._posix_process_group_kwargs() == {}


def test_terminate_process_without_psutil_prefers_killpg_for_isolated_group() -> None:
    """Regression: terminate the process group when the target has its own group."""

    with mock.patch.object(migration_server.os, "name", "posix"), mock.patch.object(
        migration_server.os, "getpgid", return_value=900, create=True
    ) as mocked_getpgid, mock.patch.object(
        migration_server.os, "getpgrp", return_value=100, create=True
    ) as mocked_getpgrp, mock.patch.object(
        migration_server.os, "killpg"
    ) as mocked_killpg, mock.patch.object(
        migration_server.os, "kill"
    ) as mocked_kill:
        migration_server._terminate_process_without_psutil(9876)

    mocked_getpgid.assert_called_once_with(9876)
    mocked_getpgrp.assert_called_once_with()
    mocked_killpg.assert_called_once_with(900, migration_server.signal.SIGTERM)
    mocked_kill.assert_not_called()


def test_terminate_process_without_psutil_falls_back_to_kill_when_group_matches() -> None:
    """Regression: avoid killing our own group and terminate only the target process."""

    with mock.patch.object(migration_server.os, "name", "posix"), mock.patch.object(
        migration_server.os, "getpgid", return_value=100, create=True
    ), mock.patch.object(migration_server.os, "getpgrp", return_value=100, create=True), mock.patch.object(
        migration_server.os, "killpg"
    ) as mocked_killpg, mock.patch.object(migration_server.os, "kill") as mocked_kill:
        migration_server._terminate_process_without_psutil(9876)

    mocked_killpg.assert_not_called()
    mocked_kill.assert_called_once_with(9876, migration_server.signal.SIGTERM)



@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("apps/vscode/test_server.py", True),
        ("apps/vscode/README.md", False),
        (r"apps\vscode\migration_server.py", True),
        (r"apps\vscode\README.md", False),
    ],
)
def test_should_watch_file(path: str, expected: bool) -> None:
    """Regression: watcher filtering should handle POSIX and Windows-style paths."""

    assert migration_server._should_watch_file(path) is expected



def test_collect_source_mtimes_handles_windows_style_walk_paths(tmp_path: Path) -> None:
    """Regression: snapshots should not crash when os.walk yields Windows separators."""

    base_dir = tmp_path / "repo"
    source_dir = base_dir / "apps" / "vscode"
    source_dir.mkdir(parents=True)
    target = source_dir / "migration_server.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    windows_root = str(source_dir).replace("/", "\\")

    with mock.patch.object(migration_server.os, "walk", return_value=[(windows_root, [], ["migration_server.py"]) ]):
        snapshot = migration_server.collect_source_mtimes(base_dir)

    assert set(snapshot) == {"apps/vscode/migration_server.py"}

def test_collect_source_mtimes_preserves_literal_backslashes_on_posix(tmp_path: Path) -> None:
    """Regression: literal backslashes in POSIX filenames should not be rewritten."""

    base_dir = tmp_path / r"repo\name"
    source_dir = base_dir / "apps" / "vscode"
    source_dir.mkdir(parents=True)
    target = source_dir / "migration_server.py"
    target.write_text("print('ok')\n", encoding="utf-8")

    snapshot = migration_server.collect_source_mtimes(base_dir)

    assert set(snapshot) == {"apps/vscode/migration_server.py"}

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


def test_cleanup_invalid_site_packages_distributions_removes_setuptools_artifacts(tmp_path: Path) -> None:
    """Regression: stale ``~setuptools`` metadata should be deleted before pip runs."""

    site_packages = tmp_path / "site-packages"
    site_packages.mkdir(parents=True)
    bad_dir = site_packages / "~etuptools-72.0.0.dist-info"
    bad_dir.mkdir()
    bad_file = site_packages / "~setuptools"
    bad_file.write_text("stale", encoding="utf-8")
    safe_file = site_packages / "~my-local-package"
    safe_file.write_text("keep", encoding="utf-8")

    with mock.patch.object(migration_server, "_site_packages_paths", return_value=[site_packages]):
        cleaned = migration_server._cleanup_invalid_site_packages_distributions()

    assert bad_dir in cleaned
    assert bad_file in cleaned
    assert bad_dir.exists() is False
    assert bad_file.exists() is False
    assert safe_file.exists() is True


def test_cleanup_invalid_site_packages_distributions_skips_unreadable_path() -> None:
    """Cleanup should be best-effort when a site-packages path cannot be read."""

    with mock.patch.object(migration_server, "_site_packages_paths") as mocked_paths:
        unreadable = mock.Mock()
        unreadable.exists.return_value = True
        unreadable.is_dir.return_value = True
        unreadable.iterdir.side_effect = PermissionError("blocked")
        mocked_paths.return_value = [unreadable]

        assert migration_server._cleanup_invalid_site_packages_distributions() == []


def test_cleanup_invalid_site_packages_distributions_ignores_backup_like_metadata(tmp_path: Path) -> None:
    """Only true metadata suffixes should be removed, not backup-like names."""

    site_packages = tmp_path / "site-packages"
    site_packages.mkdir(parents=True)
    backup = site_packages / "~something.dist-info.bak"
    backup.write_text("keep", encoding="utf-8")

    with mock.patch.object(migration_server, "_site_packages_paths", return_value=[site_packages]):
        cleaned = migration_server._cleanup_invalid_site_packages_distributions()

    assert backup not in cleaned
    assert backup.exists() is True


def test_update_requirements_cleans_invalid_site_packages_before_install(tmp_path: Path) -> None:
    """Ensure requirements installation performs stale metadata cleanup first."""

    req_file = tmp_path / "requirements.txt"
    req_file.write_text("django==5.0\n", encoding="utf-8")

    hash_file = tmp_path / ".locks" / "requirements.sha256"
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text("different", encoding="utf-8")

    completed = mock.Mock(returncode=0)
    with mock.patch.object(migration_server.subprocess, "run", return_value=completed), mock.patch.object(
        migration_server, "_windows_process_group_kwargs", return_value={}
    ), mock.patch.object(
        migration_server,
        "_cleanup_invalid_site_packages_distributions",
        return_value=[Path("/tmp/~etuptools")],
    ) as mocked_cleanup:
        assert migration_server.update_requirements(tmp_path) is True

    mocked_cleanup.assert_called_once_with()


def test_update_requirements_passes_windows_process_group_kwargs(tmp_path: Path) -> None:
    """Ensure dependency installation uses Windows process-group kwargs."""

    req_file = tmp_path / "requirements.txt"
    req_file.write_text("django==5.0\n", encoding="utf-8")

    hash_file = tmp_path / ".locks" / "requirements.sha256"
    hash_file.parent.mkdir(parents=True, exist_ok=True)
    hash_file.write_text("different", encoding="utf-8")

    completed = mock.Mock(returncode=0)
    with mock.patch.object(migration_server.subprocess, "run", return_value=completed) as mocked_run, mock.patch.object(
        migration_server, "_windows_process_group_kwargs", return_value={"creationflags": 512}
    ):
        assert migration_server.update_requirements(tmp_path) is True

    _command, kwargs = mocked_run.call_args
    assert kwargs["creationflags"] == 512


def test_is_debugger_session_detects_debugpy_variable() -> None:
    """Regression: debugpy launch variables should enable interrupt retry mode."""

    assert migration_server._is_debugger_session({"DEBUGPY_LAUNCHER_PORT": "12345"}) is True


def test_is_debugger_session_returns_false_without_debugger_variables() -> None:
    """Regression: regular shell sessions should not suppress interrupts."""

    assert migration_server._is_debugger_session({}) is False


def test_main_handles_interrupt_while_installing_requirements(monkeypatch) -> None:
    """Regression: Ctrl+C during dependency install should exit the server cleanly."""

    monkeypatch.setattr(
        migration_server,
        "update_requirements",
        mock.Mock(side_effect=KeyboardInterrupt),
    )
    monkeypatch.setattr(migration_server, "collect_source_mtimes", mock.Mock())
    monkeypatch.setattr(migration_server, "run_env_refresh_with_report", mock.Mock())

    assert migration_server.main([]) == 0
