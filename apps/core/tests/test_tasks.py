from __future__ import annotations

import json
import time

from apps.core import tasks


def test_is_migration_server_running_skips_unexpected_pid(monkeypatch, tmp_path):
    state_path = tmp_path / "migration_server.json"
    state_path.write_text(
        json.dumps({"pid": 1234, "timestamp": time.time()}), encoding="utf-8"
    )

    calls = []

    def fake_cmdline(pid: int):
        calls.append("cmdline")
        return ["python", "/opt/other_service.py"]

    def fake_start_time(pid: int):
        return time.time()

    def fake_kill(pid: int, signal: int):
        calls.append("kill")

    monkeypatch.setattr(tasks, "_read_process_cmdline", fake_cmdline)
    monkeypatch.setattr(tasks, "_read_process_start_time", fake_start_time)
    monkeypatch.setattr(tasks.os, "kill", fake_kill)

    assert tasks._is_migration_server_running(tmp_path) is False
    assert "kill" not in calls


def test_is_migration_server_running_validates_process_identity(monkeypatch, tmp_path):
    state_path = tmp_path / "migration_server.json"
    started_at = time.time()
    state_path.write_text(
        json.dumps({"pid": 5678, "timestamp": started_at}), encoding="utf-8"
    )

    def fake_cmdline(pid: int):
        script_path = tmp_path.parent / "scripts" / "migration_server.py"
        return ["python", str(script_path)]

    def fake_start_time(pid: int):
        return started_at + 30

    def fake_kill(pid: int, signal: int):
        return None

    monkeypatch.setattr(tasks, "_read_process_cmdline", fake_cmdline)
    monkeypatch.setattr(tasks, "_read_process_start_time", fake_start_time)
    monkeypatch.setattr(tasks.os, "kill", fake_kill)

    assert tasks._is_migration_server_running(tmp_path) is True
