"""Tests for the migration server helpers."""

from __future__ import annotations

import multiprocessing
import os
import sys
import time

import psutil

from apps.vscode import migration_server


def test_stop_django_server_terminates_runserver_process(tmp_path) -> None:
    """Ensure stopping the server terminates the spawned runserver process."""

    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    env = os.environ.copy()

    process = multiprocessing.Process(
        target=migration_server._run_django_server,  # type: ignore[attr-defined]
        args=(command,),
        kwargs={"cwd": tmp_path, "env": env},
        daemon=True,
    )

    process.start()

    server_proc = psutil.Process(process.pid)
    target_pid: int | None = None
    for _ in range(30):
        children = server_proc.children(recursive=True)
        if children:
            target_pid = children[0].pid
            break
        if server_proc.is_running():
            target_pid = server_proc.pid
            break
        time.sleep(0.1)

    assert target_pid is not None

    try:
        migration_server.stop_django_server(process)
        time.sleep(0.2)
        assert not psutil.pid_exists(target_pid)
    finally:
        migration_server.stop_django_server(process)
        if psutil.pid_exists(target_pid):
            try:
                psutil.Process(target_pid).kill()
            except psutil.NoSuchProcess:
                pass


