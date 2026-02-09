"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
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


def test_resolve_base_dir_prefers_workspace_env(tmp_path) -> None:
    """Prefer a VS Code workspace folder when it looks like the repo root."""

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "manage.py").write_text("# stub", encoding="utf-8")
    (repo_dir / "env-refresh.py").write_text("# stub", encoding="utf-8")

    resolved = migration_server.resolve_base_dir(
        env={"VSCODE_WORKSPACE_FOLDER": str(repo_dir)},
        cwd=tmp_path,
    )

    assert resolved == repo_dir.resolve()
