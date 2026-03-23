"""Tests for desktop shortcut synchronization services."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.utils import OperationalError

from apps.desktop.models import DesktopShortcut
from apps.desktop.services import (
    DesktopSyncResult,
    _build_exec,
    should_install_shortcut,
    sync_desktop_shortcuts,
)

pytestmark = [pytest.mark.django_db]


class _NodeStub:
    """Simple node stub that reports all requested features as present."""

    @staticmethod
    def has_feature(_slug: str) -> bool:
        """Return ``True`` for every feature slug lookup."""

        return True


def test_sync_desktop_shortcuts_installs_applications_only(
    monkeypatch, tmp_path: Path
) -> None:
    """Applications-only shortcuts should not create launchers on the desktop."""

    User = get_user_model()
    User.objects.create_user(username="tester")

    shortcut = DesktopShortcut.objects.create(
        slug="public-site-applications",
        desktop_filename="Arthexis Public Site Applications",
        name="Arthexis Public Site",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        install_location=DesktopShortcut.InstallLocation.APPLICATIONS,
        target_url="http://127.0.0.1:{port}/",
    )

    desktop_dir = tmp_path / "Desktop"
    applications_dir = tmp_path / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    applications_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "apps.desktop.services.detect_desktop_dir",
        lambda _base_dir, _username: desktop_dir,
    )
    monkeypatch.setattr(
        "apps.desktop.services.detect_applications_dir",
        lambda _base_dir, _username: applications_dir,
    )
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _NodeStub())

    result = sync_desktop_shortcuts(
        base_dir=Path("/home/tester/arthexis"), username="tester", port=8000
    )

    applications_target = applications_dir / f"{shortcut.desktop_filename}.desktop"
    desktop_target = desktop_dir / f"{shortcut.desktop_filename}.desktop"

    assert result.installed >= 1
    assert applications_target.exists()
    assert not desktop_target.exists()
    assert "Exec=" in applications_target.read_text(encoding="utf-8")
    assert "webbrowser -t http://127.0.0.1:8000/" in applications_target.read_text(
        encoding="utf-8"
    )


def test_sync_desktop_shortcuts_removes_stale_file_when_location_changes(
    monkeypatch, tmp_path: Path
) -> None:
    """Managed desktop files are removed when shortcut is moved to applications only."""

    User = get_user_model()
    User.objects.create_user(username="tester")

    shortcut = DesktopShortcut.objects.create(
        slug="admin-console-applications",
        desktop_filename="Arthexis Admin Console Applications",
        name="Arthexis Admin Console",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        install_location=DesktopShortcut.InstallLocation.APPLICATIONS,
        target_url="http://127.0.0.1:{port}/admin/",
    )

    desktop_dir = tmp_path / "Desktop"
    applications_dir = tmp_path / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    applications_dir.mkdir(parents=True, exist_ok=True)

    stale_desktop_file = desktop_dir / f"{shortcut.desktop_filename}.desktop"
    stale_desktop_file.write_text(
        "[Desktop Entry]\nX-Arthexis-Managed=true\n", encoding="utf-8"
    )

    monkeypatch.setattr(
        "apps.desktop.services.detect_desktop_dir",
        lambda _base_dir, _username: desktop_dir,
    )
    monkeypatch.setattr(
        "apps.desktop.services.detect_applications_dir",
        lambda _base_dir, _username: applications_dir,
    )
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _NodeStub())

    result = sync_desktop_shortcuts(
        base_dir=Path("/home/tester/arthexis"), username="tester", port=8000
    )

    applications_target = applications_dir / f"{shortcut.desktop_filename}.desktop"

    assert result.installed >= 1
    assert result.removed >= 1
    assert applications_target.exists()
    assert not stale_desktop_file.exists()


def test_should_install_shortcut_does_not_execute_shell_commands(
    monkeypatch, tmp_path: Path
) -> None:
    """Structured conditions should be evaluated without spawning subprocesses."""

    User = get_user_model()
    User.objects.create_user(username="tester", is_staff=True)

    shortcut = DesktopShortcut.objects.create(
        slug="public-site-conditions",
        desktop_filename="Arthexis Public Site Conditions",
        name="Arthexis Public Site",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        condition_expression="has_desktop_ui and is_staff and has_feature('user-desktop')",
    )

    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir()

    monkeypatch.setattr(
        "apps.desktop.services.detect_desktop_dir",
        lambda _base_dir, _username: desktop_dir,
    )
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _NodeStub())

    def _unexpected_run(*args, **kwargs):
        raise AssertionError(f"subprocess.run should not be called: {args!r} {kwargs!r}")

    monkeypatch.setattr("apps.desktop.services.subprocess.run", _unexpected_run)

    assert should_install_shortcut(
        shortcut,
        base_dir=Path("/home/tester/arthexis"),
        username="tester",
    ) is True


def test_build_exec_always_uses_browser_helper() -> None:
    """Desktop launchers always route through the Python browser helper."""

    shortcut = DesktopShortcut(
        slug="browser-helper",
        desktop_filename="Browser Helper",
        name="Browser Helper",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="https://example.com:{port}/status",
    )

    exec_value = _build_exec(shortcut, 8443)

    assert "webbrowser" in exec_value
    assert "https://example.com:8443/status" in exec_value


def test_sync_desktop_shortcuts_marks_db_unavailable_and_logs_warning(
    monkeypatch, caplog
) -> None:
    """Database availability failures should be visible and distinguishable in results."""

    def _raise_operational_error() -> None:
        raise OperationalError("database is down")

    caplog.set_level("WARNING")
    monkeypatch.setattr(
        "apps.desktop.services.DesktopShortcut.objects.exists", _raise_operational_error
    )

    result = sync_desktop_shortcuts(
        base_dir=Path("/home/tester/arthexis"), username="tester", port=8000
    )

    assert result.skipped_db_unavailable is True
    assert "database is unavailable" in caplog.text


def test_sync_desktop_shortcuts_command_raises_when_db_is_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    """The management command should fail fast when sync is skipped for DB availability."""

    def _skip_sync(**_kwargs) -> DesktopSyncResult:
        return DesktopSyncResult(skipped_db_unavailable=True)

    monkeypatch.setattr(
        "apps.desktop.management.commands.sync_desktop_shortcuts.sync_desktop_shortcuts",
        _skip_sync,
    )

    with pytest.raises(CommandError, match="database is unavailable"):
        call_command(
            "sync_desktop_shortcuts",
            base_dir=str(tmp_path / "arthexis"),
            username="tester",
            port=8000,
        )
