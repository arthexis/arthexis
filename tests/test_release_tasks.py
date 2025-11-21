import subprocess
import types
from datetime import datetime
import os
from pathlib import Path
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

import core.tasks as tasks


class CommandRecorder:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def find(self, executable: str):
        for args, kwargs in self.calls:
            if args and args[0] and args[0][0] == executable:
                return args, kwargs
        return None


def _setup_tmp(monkeypatch, tmp_path):
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    fake_file = core_dir / "tasks.py"
    fake_file.write_text("")
    monkeypatch.setattr(tasks, "__file__", str(fake_file))
    monkeypatch.setattr(tasks.settings, "BASE_DIR", tmp_path, raising=False)
    return tmp_path


def _write_delegate_script(base_dir: Path, unit_name: str = "delegate-unit", exit_code: int = 0):
    script = base_dir / "delegated-upgrade.sh"
    script.write_text(
        """#!/usr/bin/env bash
echo 'UNIT_NAME={unit}'
exit {code}
""".format(unit=unit_name, code=exit_code)
    )
    script.chmod(0o755)
    return script


@pytest.mark.role("Watchtower")
def test_no_upgrade_triggers_startup(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "1.0")

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert scheduled == []
    assert run_recorder.calls
    fetch_args, fetch_kwargs = run_recorder.calls[0]
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find(str(base / "delegated-upgrade.sh")) is None


@pytest.mark.role("Watchtower")
def test_upgrade_shows_message(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")
    _write_delegate_script(base)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "2.0")

    notify_calls = []
    import core.notifications as notifications

    monkeypatch.setattr(
        notifications,
        "notify",
        lambda subject, body="": notify_calls.append((subject, body)),
    )

    fake_now = datetime(2024, 3, 1, 21, 2, tzinfo=ZoneInfo("UTC"))
    local_zone = ZoneInfo("America/Monterrey")
    seen_times: dict[str, datetime] = {}

    def fake_now_func():
        return fake_now

    def fake_localtime(value):
        seen_times["localtime_arg"] = value
        return fake_now.astimezone(local_zone)

    monkeypatch.setattr(tasks.timezone, "now", fake_now_func)
    monkeypatch.setattr(tasks.timezone, "localtime", fake_localtime)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert seen_times.get("localtime_arg") is fake_now
    expected_body = fake_now.astimezone(local_zone).strftime("@ %Y%m%d %H:%M")
    assert any(
        subject == "Upgrading..." and body == expected_body
        for subject, body in notify_calls
    )
    upgrade_call = run_recorder.find(str(base / "delegated-upgrade.sh"))
    assert upgrade_call is not None
    upgrade_args, upgrade_kwargs = upgrade_call
    assert upgrade_args[0][1:] == ["./upgrade.sh", "--stable"]
    assert upgrade_kwargs.get("cwd") == base
    assert upgrade_kwargs.get("check") is False
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert scheduled
    first_call = scheduled[0]
    assert first_call["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS
    assert first_call["kwargs"].get("kwargs") == {"attempt": 1}


@pytest.mark.role("Watchtower")
def test_upgrade_detaches_when_running_under_systemd(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")
    _write_delegate_script(base)

    locks = base / "locks"
    locks.mkdir()
    (locks / "service.lck").write_text("myapp")

    watcher = tmp_path / "watch-upgrade"
    watcher.write_text("#!/bin/true")
    watcher.chmod(0o755)
    monkeypatch.setattr(tasks, "WATCH_UPGRADE_BINARY", watcher)

    monkeypatch.setenv("INVOCATION_ID", "auto-upgrade-test")

    def fake_which(name):
        if name == "systemd-run":
            return "/bin/systemd-run"
        if name == "systemctl":
            return "/bin/systemctl"
        if name == "sudo":
            return None
        return None

    monkeypatch.setattr(tasks.shutil, "which", fake_which)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "2.0")

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    upgrade_call = run_recorder.find(str(base / "delegated-upgrade.sh"))
    assert upgrade_call is not None
    delegated_args, _ = upgrade_call
    assert delegated_args[0][1:] == ["./upgrade.sh", "--stable"]
    assert scheduled


@pytest.mark.role("Watchtower")
def test_upgrade_detach_falls_back_when_systemd_run_fails(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.0")
    _write_delegate_script(base)

    locks = base / "locks"
    locks.mkdir()
    (locks / "service.lck").write_text("myapp")

    watcher = tmp_path / "watch-upgrade"
    watcher.write_text("#!/bin/true")
    watcher.chmod(0o755)
    monkeypatch.setattr(tasks, "WATCH_UPGRADE_BINARY", watcher)

    monkeypatch.setenv("INVOCATION_ID", "auto-upgrade-test")

    def fake_which(name):
        if name == "systemd-run":
            return "/bin/systemd-run"
        if name == "systemctl":
            return "/bin/systemctl"
        if name == "sudo":
            return None
        return None

    monkeypatch.setattr(tasks.shutil, "which", fake_which)
    monkeypatch.setattr(tasks.subprocess, "check_output", lambda *a, **k: "2.0")

    class FailingSystemdRun(CommandRecorder):
        def __call__(self, *args, **kwargs):
            if args and args[0] and os.path.basename(args[0][0]) == "delegated-upgrade.sh":
                return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="failed")
            return super().__call__(*args, **kwargs)

    run_recorder = FailingSystemdRun()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    upgrade_calls = [
        args
        for args, _ in run_recorder.calls
        if args and os.path.basename(args[0][0]) == "delegated-upgrade.sh"
    ]

    assert upgrade_calls
    assert not scheduled


@pytest.mark.role("Watchtower")
def test_stable_mode_skips_patch_upgrade(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.2.3")
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("stable")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "1.2.4"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert scheduled == []
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find(str(base / "delegated-upgrade.sh")) is None


@pytest.mark.role("Watchtower")
def test_stable_mode_triggers_minor_upgrade(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    (base / "VERSION").write_text("1.2.3")
    _write_delegate_script(base)
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("stable")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "1.3.0"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    notify_calls = []
    import core.notifications as notifications

    monkeypatch.setattr(
        notifications,
        "notify",
        lambda subject, body="": notify_calls.append((subject, body)),
    )

    fake_now = datetime(2024, 3, 1, 21, 2, tzinfo=ZoneInfo("UTC"))
    local_zone = ZoneInfo("America/Monterrey")
    seen_times: dict[str, datetime] = {}

    def fake_now_func():
        return fake_now

    def fake_localtime(value):
        seen_times["localtime_arg"] = value
        return fake_now.astimezone(local_zone)

    monkeypatch.setattr(tasks.timezone, "now", fake_now_func)
    monkeypatch.setattr(tasks.timezone, "localtime", fake_localtime)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert seen_times.get("localtime_arg") is fake_now
    expected_body = fake_now.astimezone(local_zone).strftime("@ %Y%m%d %H:%M")
    assert any(
        subject == "Upgrading..." and body == expected_body
        for subject, body in notify_calls
    )
    upgrade_call = run_recorder.find(str(base / "delegated-upgrade.sh"))
    assert upgrade_call is not None
    upgrade_args, upgrade_kwargs = upgrade_call
    assert upgrade_args[0][1:] == ["./upgrade.sh", "--stable"]
    assert upgrade_kwargs.get("cwd") == base
    assert upgrade_kwargs.get("check") is False
    fetch_call = run_recorder.calls[0]
    fetch_args, fetch_kwargs = fetch_call
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert scheduled
    first_call = scheduled[0]
    assert first_call["kwargs"].get("countdown") == tasks.AUTO_UPGRADE_HEALTH_DELAY_SECONDS
    assert first_call["kwargs"].get("kwargs") == {"attempt": 1}


@pytest.mark.role("Watchtower")
def test_verify_auto_upgrade_health_records_failure(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    locks = base / "locks"
    locks.mkdir()

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    def fake_urlopen(*args, **kwargs):
        raise URLError("down")

    monkeypatch.setattr(tasks.urllib.request, "urlopen", fake_urlopen)

    run_calls = []

    def fake_run(args, cwd=None, check=None):
        run_calls.append({"args": args, "cwd": cwd, "check": check})
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)

    monkeypatch.setattr(
        tasks.subprocess,
        "check_output",
        lambda *a, **k: "deadbeef",
    )

    recorded_failover: dict[str, object] = {}

    def capture_failover(base_dir, *, reason, detail=None, revision=None):
        recorded_failover["base_dir"] = base_dir
        recorded_failover["reason"] = reason
        recorded_failover["detail"] = detail
        recorded_failover["revision"] = revision

    monkeypatch.setattr(tasks, "write_failover_lock", capture_failover)

    result = tasks.verify_auto_upgrade_health.run(attempt=1)
    assert result is False
    assert not scheduled
    assert not run_calls

    skip_file = locks / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME
    assert skip_file.exists()
    assert skip_file.read_text().strip() == "deadbeef"

    assert recorded_failover["base_dir"] == base
    assert "failed" in str(recorded_failover["detail"])
    assert recorded_failover["revision"] == "deadbeef"


@pytest.mark.role("Watchtower")
def test_check_github_updates_skips_blocked_revision(monkeypatch, tmp_path):
    base = _setup_tmp(monkeypatch, tmp_path)
    locks = base / "locks"
    locks.mkdir()
    (locks / "auto_upgrade.lck").write_text("latest")
    (locks / tasks.AUTO_UPGRADE_SKIP_LOCK_NAME).write_text("blocked\n")

    def fake_check_output(args, cwd=None, **kwargs):
        if args[:3] == ["git", "rev-parse", "main"]:
            return "local"
        if args[:3] == ["git", "rev-parse", "origin/main"]:
            return "blocked"
        if args[:3] == ["git", "show", "origin/main:VERSION"]:
            return "2.0"
        raise AssertionError(f"Unexpected command: {args}")

    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    run_recorder = CommandRecorder()
    monkeypatch.setattr(tasks.subprocess, "run", run_recorder)

    scheduled = []

    def fake_apply_async(*args, **kwargs):
        scheduled.append((args, kwargs))

    monkeypatch.setattr(
        tasks.verify_auto_upgrade_health,
        "apply_async",
        fake_apply_async,
    )

    tasks.check_github_updates()

    assert scheduled == []
    assert run_recorder.calls
    fetch_args, fetch_kwargs = run_recorder.calls[0]
    assert fetch_args[0][:3] == ["git", "fetch", "origin"]
    assert fetch_kwargs.get("cwd") == base
    assert fetch_kwargs.get("check") is True
    assert run_recorder.find(str(base / "delegated-upgrade.sh")) is None
