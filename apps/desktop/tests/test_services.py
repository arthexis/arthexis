"""Tests for desktop shortcut synchronization services."""

from __future__ import annotations

from pathlib import Path

import pytest

from django.contrib.auth import get_user_model

from apps.desktop.models import DesktopShortcut
from apps.desktop.services import sync_desktop_shortcuts


pytestmark = pytest.mark.django_db


class _NodeStub:
    """Simple node stub that reports all requested features as present."""

    @staticmethod
    def has_feature(_slug: str) -> bool:
        """Return ``True`` for every feature slug lookup."""

        return True


def test_sync_desktop_shortcuts_installs_applications_only(monkeypatch, tmp_path: Path) -> None:
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

    monkeypatch.setattr("apps.desktop.services.detect_desktop_dir", lambda _base_dir, _username: desktop_dir)
    monkeypatch.setattr(
        "apps.desktop.services.detect_applications_dir", lambda _base_dir, _username: applications_dir
    )
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _NodeStub())

    result = sync_desktop_shortcuts(base_dir=Path("/home/tester/arthexis"), username="tester", port=8000)

    applications_target = applications_dir / f"{shortcut.desktop_filename}.desktop"
    desktop_target = desktop_dir / f"{shortcut.desktop_filename}.desktop"

    assert result.installed >= 1
    assert applications_target.exists()
    assert not desktop_target.exists()


def test_sync_desktop_shortcuts_removes_stale_file_when_location_changes(monkeypatch, tmp_path: Path) -> None:
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
    stale_desktop_file.write_text("[Desktop Entry]\nX-Arthexis-Managed=true\n", encoding="utf-8")

    monkeypatch.setattr("apps.desktop.services.detect_desktop_dir", lambda _base_dir, _username: desktop_dir)
    monkeypatch.setattr(
        "apps.desktop.services.detect_applications_dir", lambda _base_dir, _username: applications_dir
    )
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _NodeStub())

    result = sync_desktop_shortcuts(base_dir=Path("/home/tester/arthexis"), username="tester", port=8000)

    applications_target = applications_dir / f"{shortcut.desktop_filename}.desktop"

    assert result.installed >= 1
    assert result.removed >= 1
    assert applications_target.exists()
    assert not stale_desktop_file.exists()
