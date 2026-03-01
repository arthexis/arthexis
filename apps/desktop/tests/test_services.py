"""Regression tests for desktop shortcut synchronization services."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.desktop.models import DesktopShortcut
from apps.desktop.services import render_shortcut_desktop_entry, should_install_shortcut, sync_desktop_shortcuts
from apps.nodes.models import NodeFeature


class _StubNode:
    """Minimal local node stub used for desktop shortcut eligibility tests."""

    def __init__(self, features: set[str]) -> None:
        self._features = set(features)

    def has_feature(self, slug: str) -> bool:
        """Return whether the stub node exposes the requested feature."""

        return slug in self._features


@pytest.mark.django_db
def test_should_install_shortcut_requires_enabled_node_feature(settings, monkeypatch) -> None:
    """Shortcuts requiring a node feature are skipped when the feature is disabled."""

    settings.BASE_DIR = "/home/tester/arthexis"
    feature = NodeFeature.objects.get(slug="user-desktop")

    shortcut = DesktopShortcut.objects.create(
        slug="public-site-test",
        desktop_filename="Arthexis Public Site Test",
        name="Arthexis Public Site Test",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        require_desktop_ui=False,
    )
    shortcut.required_features.add(feature)

    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode(set()))
    assert should_install_shortcut(shortcut, base_dir=Path(settings.BASE_DIR), username="tester") is False

    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode({"user-desktop"}))
    assert should_install_shortcut(shortcut, base_dir=Path(settings.BASE_DIR), username="tester") is True


@pytest.mark.django_db
def test_sync_desktop_shortcuts_writes_desktop_entries(settings, monkeypatch, tmp_path) -> None:
    """Sync operation writes launcher files into the detected desktop directory."""

    settings.BASE_DIR = "/home/tester/arthexis"
    shortcut = DesktopShortcut.objects.create(
        slug="admin-console-test",
        desktop_filename="Arthexis Admin Console Test",
        name="Arthexis Admin Console Test",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/admin/",
        icon_name="applications-system",
        require_desktop_ui=False,
    )

    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir(parents=True)

    monkeypatch.setattr("apps.desktop.services.detect_desktop_dir", lambda *args, **kwargs: desktop_dir)
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode(set()))

    result = sync_desktop_shortcuts(base_dir=Path(settings.BASE_DIR), username="tester", port=9000)

    rendered = (desktop_dir / f"{shortcut.desktop_filename}.desktop").read_text(encoding="utf-8")
    assert "Name=Arthexis Admin Console Test" in rendered
    assert "http://127.0.0.1:9000/admin/" in rendered
    assert result.installed == 1


@pytest.mark.django_db
def test_should_install_shortcut_rejects_unsafe_expression(settings, monkeypatch) -> None:
    """Unsafe expression syntax is rejected instead of being evaluated."""

    settings.BASE_DIR = "/home/tester/arthexis"
    shortcut = DesktopShortcut.objects.create(
        slug="unsafe-expression-test",
        desktop_filename="Unsafe Expression Test",
        name="Unsafe Expression Test",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        require_desktop_ui=False,
        condition_expression="(1).__class__",
    )

    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode(set()))
    assert should_install_shortcut(shortcut, base_dir=Path(settings.BASE_DIR), username="tester") is False


@pytest.mark.django_db
def test_render_shortcut_desktop_entry_sanitizes_newlines() -> None:
    """Rendered desktop files collapse CR/LF characters from user-provided values."""

    shortcut = DesktopShortcut(
        slug="sanitize-test",
        desktop_filename="Sanitize Test",
        name="Safe Name\nExec=evil",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        comment="Hello\r\nWorld",
        categories="Utility;\nOther;",
        extra_entries={"X-Test\nKey": "value\nwith-break"},
    )

    rendered = render_shortcut_desktop_entry(shortcut, exec_value="echo ok\nrm -rf /", icon_value="icon\nname")
    assert "Name=Safe Name Exec=evil" in rendered
    assert "Comment=Hello  World" in rendered
    assert "Exec=echo ok rm -rf /" in rendered
    assert "Icon=icon name" in rendered
    assert "X-Test Key=value with-break" in rendered
    assert "X-Arthexis-Managed=true" in rendered


@pytest.mark.django_db
def test_sync_desktop_shortcuts_removes_only_managed_stale_files(settings, monkeypatch, tmp_path) -> None:
    """Stale cleanup removes outdated managed files but keeps unmanaged launchers."""

    settings.BASE_DIR = "/home/tester/arthexis"
    DesktopShortcut.objects.create(
        slug="managed-current",
        desktop_filename="Current Launcher",
        name="Current Launcher",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        require_desktop_ui=False,
    )

    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir(parents=True)
    (desktop_dir / "Old Managed.desktop").write_text("[Desktop Entry]\nX-Arthexis-Managed=true\n", encoding="utf-8")
    (desktop_dir / "Unmanaged.desktop").write_text("[Desktop Entry]\nName=Other\n", encoding="utf-8")

    monkeypatch.setattr("apps.desktop.services.detect_desktop_dir", lambda *args, **kwargs: desktop_dir)
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode(set()))

    result = sync_desktop_shortcuts(base_dir=Path(settings.BASE_DIR), username="tester", port=9000)

    assert not (desktop_dir / "Old Managed.desktop").exists()
    assert (desktop_dir / "Unmanaged.desktop").exists()
    assert result.removed == 1
