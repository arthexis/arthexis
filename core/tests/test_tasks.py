from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import django
import pytest
from django.test import override_settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


def test_read_remote_version_handles_missing_git(monkeypatch, tmp_path):
    """_read_remote_version should return ``None`` when git is unavailable."""

    from core import tasks

    def _missing_git(*args, **kwargs):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(tasks.subprocess, "check_output", _missing_git)

    result = tasks._read_remote_version(tmp_path, "main")

    assert result is None


def test_project_base_dir_prefers_settings(tmp_path):
    """_project_base_dir should return the configured ``BASE_DIR`` when set."""

    from core import tasks

    with override_settings(BASE_DIR=tmp_path):
        assert tasks._project_base_dir() == tmp_path

    with override_settings(BASE_DIR=str(tmp_path)):
        assert tasks._project_base_dir() == tmp_path


def test_check_github_updates_uses_project_base_dir(monkeypatch, tmp_path):
    """The auto-upgrade task should honor ``settings.BASE_DIR`` for log writes."""

    from core import tasks

    class Sentinel(RuntimeError):
        pass

    log_path = tmp_path / "logs" / "auto-upgrade.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    recorded_dirs: list[Path] = []

    def fake_log_path(base_dir: Path) -> Path:
        recorded_dirs.append(base_dir)
        return log_path

    def fake_append(*args, **kwargs):
        return None

    def fake_load_skipped(base_dir: Path):
        assert base_dir == tmp_path
        raise Sentinel()

    def fake_run(command, *args, **kwargs):
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if command[-1].startswith("origin/"):
            return "remote-sha"
        return "local-sha"

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", fake_append)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", fake_load_skipped)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_reset_network_failure_count", lambda base: None)
    monkeypatch.setattr(tasks.shutil, "which", lambda command: None)
    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    with override_settings(BASE_DIR=tmp_path):
        with pytest.raises(Sentinel):
            tasks.check_github_updates()

    assert recorded_dirs == [tmp_path]


def test_check_github_updates_handles_mode_read_error(monkeypatch, tmp_path):
    """The auto-upgrade task should ignore unreadable mode lock files."""

    from core import tasks

    base_dir = Path(tasks.__file__).resolve().parent.parent
    mode_path = base_dir / "locks" / "auto_upgrade.lck"

    original_exists = Path.exists
    original_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        if self == mode_path:
            return True
        return original_exists(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == mode_path:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "read_text", fake_read_text, raising=False)

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    log_path = tmp_path / "auto-upgrade.log"
    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", lambda base: log_path)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(
        tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL
    )
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")

    monkeypatch.setattr(tasks.shutil, "which", lambda command: None)

    def fake_run(command, *args, **kwargs):
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            return "abcdef123456"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates()


def test_check_github_updates_treats_latest_mode_case_insensitively(
    monkeypatch, tmp_path
):
    """Uppercase auto-upgrade mode values should still trigger latest upgrades."""

    from core import tasks

    base_dir = Path(tasks.__file__).resolve().parent.parent
    mode_path = base_dir / "locks" / "auto_upgrade.lck"

    original_exists = Path.exists
    original_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        if self == mode_path:
            return True
        return original_exists(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == mode_path:
            return "LATEST\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "read_text", fake_read_text, raising=False)

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    log_path = tmp_path / "auto-upgrade.log"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(
        tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL
    )
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.1.26")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.1.25")

    monkeypatch.setattr(tasks.shutil, "which", lambda command: None)

    run_commands: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            if command[-1].startswith("origin/"):
                return "remote-sha"
            return "local-sha"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates()

    assert ["./upgrade.sh", "--latest", "--no-restart"] in run_commands


@pytest.mark.parametrize(
    (
        "channel_override",
        "expected_command",
        "override_message",
        "severity_name",
    ),
    [
        ("latest", ["./upgrade.sh", "--latest", "--no-restart"], "latest", "NORMAL"),
        ("stable", ["./upgrade.sh", "--stable", "--no-restart"], "stable", "CRITICAL"),
        ("normal", ["./upgrade.sh", "--no-restart"], None, "NORMAL"),
    ],
)
def test_check_github_updates_respects_channel_override(
    monkeypatch,
    tmp_path,
    channel_override,
    expected_command,
    override_message,
    severity_name,
):
    """An explicit channel override should force the requested upgrade mode."""

    from core import tasks

    base_dir = Path(tasks.__file__).resolve().parent.parent
    mode_path = base_dir / "locks" / "auto_upgrade.lck"
    service_path = base_dir / "locks/service.lck"

    original_exists = Path.exists
    original_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        if self == mode_path:
            return True
        if self == service_path:
            return False
        return original_exists(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == mode_path:
            return "version\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "read_text", fake_read_text, raising=False)

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    log_path = tmp_path / "auto-upgrade.log"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    messages: list[str] = []
    run_commands: list[list[str]] = []

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(
        tasks,
        "_append_auto_upgrade_log",
        lambda _base, message: messages.append(message),
    )
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    severity_value = getattr(tasks, f"SEVERITY_{severity_name}")
    monkeypatch.setattr(
        tasks,
        "_resolve_release_severity",
        lambda version: severity_value,
    )
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.1.26")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.1.25")

    monkeypatch.setattr(tasks.shutil, "which", lambda command: None)

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            if command[-1].startswith("origin/"):
                return "remote-sha"
            return "local-sha"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates(channel_override=channel_override)

    assert expected_command in run_commands
    if override_message is None:
        assert all(
            "Using admin override channel" not in message for message in messages
        )
    else:
        assert any(
            f"Using admin override channel: {override_message}" in message
            for message in messages
        )


def test_check_github_updates_allows_stable_critical_patch(monkeypatch, tmp_path):
    """Stable mode should upgrade when a critical patch is available."""

    from core import tasks

    base_dir = Path(tasks.__file__).resolve().parent.parent
    mode_path = base_dir / "locks" / "auto_upgrade.lck"
    service_path = base_dir / "locks/service.lck"

    original_exists = Path.exists
    original_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        if self == mode_path:
            return True
        if self == service_path:
            return False
        return original_exists(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == mode_path:
            return "stable\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "read_text", fake_read_text, raising=False)

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    log_path = tmp_path / "auto-upgrade.log"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_CRITICAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "1.2.4")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "1.2.3")

    run_commands: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            return "remote-sha"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates()

    assert ["./upgrade.sh", "--stable", "--no-restart"] in run_commands


def test_check_github_updates_restarts_dev_server(monkeypatch, tmp_path):
    """Nodes without systemd should restart the development server after upgrade."""

    from core import tasks

    base_dir = tmp_path / "node"
    locks = base_dir / "locks"
    logs = base_dir / "logs"
    locks.mkdir(parents=True)
    logs.mkdir(parents=True)
    (base_dir / "VERSION").write_text("0.0.0", encoding="utf-8")
    (locks / "auto_upgrade.lck").write_text("latest", encoding="utf-8")
    start_script = base_dir / "start.sh"
    start_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)

    log_path = logs / "auto-upgrade.log"
    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", lambda _base: log_path)

    messages: list[str] = []
    monkeypatch.setattr(
        tasks,
        "_append_auto_upgrade_log",
        lambda _base, message: messages.append(message),
    )

    run_commands: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        if command[:2] == ["pkill", "-f"]:
            return CompletedProcess(command, 0)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if command[:3] == ["git", "rev-parse", "main"]:
            return "local"
        return ""

    popen_calls: list[list[str]] = []

    class DummyPopen:
        def __init__(self, command, *args, **kwargs):
            popen_calls.append(command)

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(tasks.subprocess, "Popen", DummyPopen)

    with override_settings(BASE_DIR=base_dir):
        tasks.check_github_updates()

    assert any(cmd[0] == "./upgrade.sh" and "--no-restart" in cmd for cmd in run_commands)
    assert popen_calls == [["./start.sh"]]
    assert any(
        "Restarting development server via start.sh" in message for message in messages
    )


def test_check_github_updates_skips_latest_low_severity_patch(monkeypatch, tmp_path):
    """Latest mode should skip low severity patches during auto-upgrade."""

    from core import tasks

    base_dir = Path(tasks.__file__).resolve().parent.parent
    mode_path = base_dir / "locks" / "auto_upgrade.lck"
    service_path = base_dir / "locks/service.lck"

    original_exists = Path.exists
    original_read_text = Path.read_text

    def fake_exists(self: Path) -> bool:
        if self == mode_path:
            return True
        if self == service_path:
            return False
        return original_exists(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if self == mode_path:
            return "latest\n"
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", fake_exists, raising=False)
    monkeypatch.setattr(Path, "read_text", fake_read_text, raising=False)

    notify_stub = SimpleNamespace(notify=lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "core.notifications", notify_stub)

    import nodes.apps as nodes_apps

    startup_calls: list[bool] = []
    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: startup_calls.append(True))

    log_path = tmp_path / "auto-upgrade.log"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    messages: list[str] = []

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda _base, message: messages.append(message))
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_LOW)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "1.2.4")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "1.2.3")

    run_commands: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            if command[-1].startswith("origin/"):
                return "remote-sha"
            return "local-sha"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates()

    assert ["./upgrade.sh", "--latest", "--no-restart"] not in run_commands
    assert any(
        message.startswith("Skipping auto-upgrade for low severity patch")
        for message in messages
    )
    assert startup_calls == [True]


def test_check_github_updates_logs_fetch_failure_details(monkeypatch, tmp_path):
    """Fetch failures should append detailed error messages to the log."""

    from core import tasks

    base_dir = tmp_path
    (base_dir / "locks").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(tasks, "_project_base_dir", lambda: base_dir)
    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", lambda _base: base_dir / "auto-upgrade.log")
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda _base: set())
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_reset_network_failure_count", lambda _base: None)
    monkeypatch.setattr(tasks, "_handle_network_failure_if_applicable", lambda _base, _exc: False)

    messages: list[str] = []

    def fake_append(_base: Path, message: str) -> None:
        messages.append(message)

    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", fake_append)

    def fake_run(command, *args, **kwargs):
        raise subprocess.CalledProcessError(
            128,
            command,
            "",  # stdout
            "fatal: forbidden\n",  # stderr with newline to ensure trimming
        )

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)

    with pytest.raises(subprocess.CalledProcessError):
        tasks.check_github_updates()

    assert messages == ["Git fetch failed (exit code 128): fatal: forbidden"]


def test_check_github_updates_network_failures_trigger_reboot(
    monkeypatch, tmp_path
):
    """Repeated network failures should trigger a reboot when safe."""

    from core import tasks

    messages: list[str] = []

    monkeypatch.setattr(
        tasks,
        "_append_auto_upgrade_log",
        lambda _base, message: messages.append(message),
    )
    log_path = tmp_path / "auto-upgrade.log"
    network_lock = tmp_path / "auto_upgrade_network_failures.lck"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")
    monkeypatch.setattr(tasks, "_network_failure_lock_path", lambda _base: network_lock)
    monkeypatch.setattr(tasks, "_charge_point_active", lambda _base: False)
    reboot_calls: list[bool] = []
    monkeypatch.setattr(tasks, "_trigger_auto_upgrade_reboot", lambda base: reboot_calls.append(True))

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["git", "fetch"]:
            raise subprocess.CalledProcessError(
                1,
                command,
                None,
                "fatal: unable to access 'https://example.com': Could not resolve host: example.com",
            )
        return CompletedProcess(command, 0)

    def fake_check_output(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("check_output should not run when fetch fails")

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    for _ in range(tasks.AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD):
        with pytest.raises(subprocess.CalledProcessError):
            tasks.check_github_updates()

    assert network_lock.read_text(encoding="utf-8") == str(
        tasks.AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD
    )
    assert reboot_calls == [True]
    assert any(
        message.startswith("Git fetch failed (exit code 1): fatal: unable to access")
        for message in messages
    )
    assert any("Rebooting due to repeated auto-upgrade network failures" in msg for msg in messages)


def test_check_github_updates_skips_reboot_when_charge_point_active(
    monkeypatch, tmp_path
):
    """An active charge point should prevent automatic reboots."""

    from core import tasks

    log_path = tmp_path / "auto-upgrade.log"
    network_lock = tmp_path / "auto_upgrade_network_failures.lck"
    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", lambda _base: log_path)
    messages: list[str] = []
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda _base, message: messages.append(message))
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")
    monkeypatch.setattr(tasks, "_network_failure_lock_path", lambda _base: network_lock)
    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    monkeypatch.setattr(tasks, "_charge_point_active", lambda _base: True)
    reboot_calls: list[bool] = []
    monkeypatch.setattr(tasks, "_trigger_auto_upgrade_reboot", lambda base: reboot_calls.append(True))

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["git", "fetch"]:
            raise subprocess.CalledProcessError(
                1,
                command,
                None,
                "fatal: unable to access 'https://example.com': Could not resolve host: example.com",
            )
        return CompletedProcess(command, 0)

    def fake_check_output(*args, **kwargs):  # pragma: no cover - should not run
        raise AssertionError("check_output should not run when fetch fails")

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    for _ in range(tasks.AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD):
        with pytest.raises(subprocess.CalledProcessError):
            tasks.check_github_updates()

    assert reboot_calls == []
    assert network_lock.read_text(encoding="utf-8") == str(
        tasks.AUTO_UPGRADE_NETWORK_FAILURE_THRESHOLD
    )
    assert any("Skipping reboot" in message for message in messages)


def test_check_github_updates_resets_network_failures_after_success(
    monkeypatch, tmp_path
):
    """Successful runs should clear the network failure lockfile."""

    from core import tasks

    log_path = tmp_path / "auto-upgrade.log"
    network_lock = tmp_path / "auto_upgrade_network_failures.lck"

    def fake_log_path(_base: Path) -> Path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        return log_path

    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", fake_log_path)
    messages: list[str] = []
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda _base, message: messages.append(message))
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")
    monkeypatch.setattr(tasks, "_network_failure_lock_path", lambda _base: network_lock)
    monkeypatch.setattr(tasks, "_charge_point_active", lambda _base: False)
    monkeypatch.setattr(tasks, "_trigger_auto_upgrade_reboot", lambda base: None)

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    fetch_attempts = {"count": 0}

    def fake_run(command, *args, **kwargs):
        if command[:2] == ["git", "fetch"]:
            fetch_attempts["count"] += 1
            if fetch_attempts["count"] == 1:
                raise subprocess.CalledProcessError(
                    1,
                    command,
                    None,
                    "fatal: unable to access 'https://example.com': Could not resolve host: example.com",
                )
            return CompletedProcess(command, 0)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if "rev-parse" in command:
            if command[-1].startswith("origin/"):
                return "remote-sha"
            return "local-sha"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    with pytest.raises(subprocess.CalledProcessError):
        tasks.check_github_updates()

    assert network_lock.read_text(encoding="utf-8") == "1"

    # Ensure subsequent run succeeds and clears the counter
    tasks.check_github_updates()

    assert not network_lock.exists()
    assert any("Auto-upgrade network failure 1" in message for message in messages)


def test_check_github_updates_reverts_when_service_restart_fails(
    monkeypatch, tmp_path
):
    """Auto-upgrade should revert when the service never reports active."""

    from core import tasks

    base_dir = tmp_path / "node"
    base_dir.mkdir()
    (base_dir / "VERSION").write_text("0.0.0")
    (base_dir / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    locks = base_dir / "locks"
    locks.mkdir()
    (locks / "service.lck").write_text("arthexis")
    (locks / "auto_upgrade.lck").write_text("latest")
    (base_dir / "logs").mkdir()

    fake_module = base_dir / "core" / "tasks.py"
    fake_module.parent.mkdir()
    fake_module.write_text("")
    monkeypatch.setattr(tasks, "__file__", str(fake_module))

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")

    schedule_calls: list[bool] = []
    monkeypatch.setattr(
        tasks,
        "_schedule_health_check",
        lambda *args, **kwargs: schedule_calls.append(True),
    )

    def fake_run(command, *args, **kwargs):
        return subprocess.CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if command[:3] == ["git", "rev-parse", "main"]:
            return "local"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    monkeypatch.setattr(
        tasks,
        "_wait_for_service_restart",
        lambda base, service, timeout=30: False,
    )

    revert_calls: list[tuple[Path, str]] = []

    def record_revert(base: Path, service: str) -> None:
        revert_calls.append((base, service))

    monkeypatch.setattr(tasks, "_revert_after_restart_failure", record_revert)

    with override_settings(BASE_DIR=base_dir):
        tasks.check_github_updates()

    assert revert_calls == [(base_dir, "arthexis")]
    assert schedule_calls == []


def test_check_github_updates_restarts_inactive_service(monkeypatch, tmp_path):
    """Inactive services after upgrade should be started via ``start.sh``."""

    from core import tasks

    base_dir = tmp_path / "node"
    base_dir.mkdir()
    (base_dir / "VERSION").write_text("0.0.0")
    (base_dir / "start.sh").write_text("#!/bin/sh\nexit 0\n")
    locks = base_dir / "locks"
    locks.mkdir()
    (locks / "service.lck").write_text("arthexis")
    (locks / "auto_upgrade.lck").write_text("latest")
    logs_dir = base_dir / "logs"
    logs_dir.mkdir()

    fake_module = base_dir / "core" / "tasks.py"
    fake_module.parent.mkdir()
    fake_module.write_text("")
    monkeypatch.setattr(tasks, "__file__", str(fake_module))

    monkeypatch.setitem(
        sys.modules,
        "core.notifications",
        SimpleNamespace(notify=lambda *args, **kwargs: None),
    )

    import nodes.apps as nodes_apps

    monkeypatch.setattr(nodes_apps, "_startup_notification", lambda: None)

    monkeypatch.setattr(tasks, "_load_skipped_revisions", lambda base: set())
    monkeypatch.setattr(tasks, "_resolve_release_severity", lambda version: tasks.SEVERITY_NORMAL)
    monkeypatch.setattr(tasks, "_read_remote_version", lambda base, branch: "0.0.1")
    monkeypatch.setattr(tasks, "_read_local_version", lambda base: "0.0.0")
    monkeypatch.setattr(tasks, "_auto_upgrade_log_path", lambda _base: logs_dir / "auto-upgrade.log")
    monkeypatch.setattr(tasks, "_reset_network_failure_count", lambda _base: None)
    monkeypatch.setattr(tasks, "_schedule_health_check", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_systemctl_command", lambda: ["systemctl"])

    messages: list[str] = []
    monkeypatch.setattr(
        tasks,
        "_append_auto_upgrade_log",
        lambda _base, message: messages.append(message),
    )

    run_commands: list[list[str]] = []

    def fake_run(command, *args, **kwargs):
        run_commands.append(command)
        if command[:2] == ["git", "fetch"]:
            return CompletedProcess(command, 0)
        if command[:2] == ["git", "pull"]:
            return CompletedProcess(command, 0)
        if command[:4] == ["systemctl", "is-active", "--quiet", "arthexis"]:
            return CompletedProcess(command, 3)
        if command[0] == "./upgrade.sh":
            return CompletedProcess(command, 0)
        if command[0] == "./start.sh":
            return CompletedProcess(command, 0)
        return CompletedProcess(command, 0)

    def fake_check_output(command, *args, **kwargs):
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return "remote"
        if command[:3] == ["git", "rev-parse", "main"]:
            return "local"
        return ""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(
        tasks,
        "_wait_for_service_restart",
        lambda base, service, timeout=30: True,
    )

    with override_settings(BASE_DIR=base_dir):
        tasks.check_github_updates()

    assert ["./start.sh"] in run_commands
    assert any(
        "Service arthexis not active after upgrade; restarting via start.sh" in message
        for message in messages
    )
    assert messages.count("Waiting for arthexis to restart after upgrade") == 1


def test_resolve_service_url_handles_case_insensitive_mode(tmp_path):
    """Public mode should be detected regardless of the file's casing."""

    from core import tasks

    base_dir = tmp_path
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()
    (lock_dir / "nginx_mode.lck").write_text("PUBLIC\n", encoding="utf-8")

    url = tasks._resolve_service_url(base_dir)

    assert url == "http://127.0.0.1:8888/"


def test_handle_failed_health_check_records_failover_lock(monkeypatch, tmp_path):
    """Reverting after a failed health check should create the failover lock."""

    from core import tasks

    recorded: dict[str, object] = {}

    monkeypatch.setattr(tasks, "_add_skipped_revision", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(tasks, "_current_revision", lambda _base: "rev123456")
    monkeypatch.setattr(
        tasks.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0),
    )

    def capture_write(base_dir, *, reason, detail=None, revision=None):
        recorded["base_dir"] = base_dir
        recorded["reason"] = reason
        recorded["detail"] = detail
        recorded["revision"] = revision

    monkeypatch.setattr(tasks, "write_failover_lock", capture_write)

    tasks._handle_failed_health_check(tmp_path, "failed with timeout")

    assert recorded["base_dir"] == tmp_path
    assert recorded["reason"] == "Auto-upgrade health check failed"
    assert recorded["detail"] == "failed with timeout"
    assert recorded["revision"] == "rev123456"


def test_revert_after_restart_failure_sets_failover_lock(monkeypatch, tmp_path):
    """Restart failures should schedule the failover alert after reverting."""

    from core import tasks

    log_messages: list[str] = []
    monkeypatch.setattr(tasks, "_append_auto_upgrade_log", lambda _base, message: log_messages.append(message))
    monkeypatch.setattr(tasks, "_systemctl_command", lambda: [])
    monkeypatch.setattr(tasks, "_current_revision", lambda _base: "cafebabe")
    monkeypatch.setattr(
        tasks.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0),
    )

    recorded: dict[str, object] = {}

    def capture_write(base_dir, *, reason, detail=None, revision=None):
        recorded["base_dir"] = base_dir
        recorded["reason"] = reason
        recorded["detail"] = detail
        recorded["revision"] = revision

    monkeypatch.setattr(tasks, "write_failover_lock", capture_write)

    tasks._revert_after_restart_failure(tmp_path, "django")

    assert recorded["base_dir"] == tmp_path
    assert "django" in str(recorded["reason"])
    assert "failover" in str(recorded["detail"])
    assert recorded["revision"] == "cafebabe"


def test_verify_auto_upgrade_health_clears_failover_lock(monkeypatch):
    """Successful health checks should remove the failover alert."""

    from core import tasks

    class DummyResponse:
        status = 200

        def getcode(self):
            return self.status

    class ContextManager:
        def __enter__(self):
            return DummyResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(tasks.urllib.request, "urlopen", lambda *args, **kwargs: ContextManager())
    monkeypatch.setattr(tasks, "_record_health_check_result", lambda *args, **kwargs: None)

    cleared: list[Path] = []

    def capture_clear(base_dir):
        cleared.append(base_dir)

    monkeypatch.setattr(tasks, "clear_failover_lock", capture_clear)

    assert tasks.verify_auto_upgrade_health(attempt=1) is True
    assert cleared, "Failover lock was not cleared after success"
