from __future__ import annotations

import os
import sys
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace

import django

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
            return b"abcdef123456"
        return b""

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
                return b"remote-sha"
            return b"local-sha"
        return b""

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
            return b"remote-sha"
        return b""

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
                return b"remote-sha"
            return b"local-sha"
        return b""

    monkeypatch.setattr(tasks.subprocess, "run", fake_run)
    monkeypatch.setattr(tasks.subprocess, "check_output", fake_check_output)

    tasks.check_github_updates()

    assert ["./upgrade.sh", "--latest", "--no-restart"] not in run_commands
    assert any(
        message.startswith("Skipping auto-upgrade for low severity patch")
        for message in messages
    )
    assert startup_calls == [True]


def test_resolve_service_url_handles_case_insensitive_mode(tmp_path):
    """Public mode should be detected regardless of the file's casing."""

    from core import tasks

    base_dir = tmp_path
    lock_dir = base_dir / "locks"
    lock_dir.mkdir()
    (lock_dir / "nginx_mode.lck").write_text("PUBLIC\n", encoding="utf-8")

    url = tasks._resolve_service_url(base_dir)

    assert url == "http://127.0.0.1:8888/"
