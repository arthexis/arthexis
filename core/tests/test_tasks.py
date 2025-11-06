from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import django
import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()


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


def test_check_github_updates_network_failures_trigger_reboot(
    monkeypatch, tmp_path
):
    """Repeated network failures should trigger a reboot when safe."""

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


def test_resolve_service_url_handles_case_insensitive_mode(tmp_path):
    """Public mode should be detected regardless of the file's casing."""

    from core import tasks

    base_dir = tmp_path
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()
    (lock_dir / "nginx_mode.lck").write_text("PUBLIC\n", encoding="utf-8")

    url = tasks._resolve_service_url(base_dir)

    assert url == "http://127.0.0.1:8888/"
