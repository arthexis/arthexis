"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
import types
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


def test_notify_async_uses_fallback_when_notifications_import_fails() -> None:
    """Verify fallback notification is used when project notifications are unavailable."""

    migration_server._resolve_notify_async.cache_clear()

    original_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "apps.core.notifications":
            raise ModuleNotFoundError("apps.core.notifications")
        return original_import(name, globals, locals, fromlist, level)

    with mock.patch("builtins.__import__", side_effect=fake_import), mock.patch(
        "builtins.print"
    ) as mocked_print:
        migration_server.notify_async("Subject", "Body")

    mocked_print.assert_called_once_with("Notification: Subject - Body")


def test_notify_async_uses_project_notifier_when_available() -> None:
    """Verify project notifier is preferred when import succeeds."""

    migration_server._resolve_notify_async.cache_clear()
    fake_module = types.ModuleType("apps.core.notifications")
    fake_notify = mock.Mock()
    fake_module.notify_async = fake_notify

    with mock.patch.dict("sys.modules", {"apps.core.notifications": fake_module}):
        migration_server.notify_async("Hello", "World")

    fake_notify.assert_called_once_with("Hello", "World")
