"""Regression tests for desktop shortcut synchronization services."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.desktop.models import DesktopShortcut
from apps.desktop.services import should_install_shortcut, sync_desktop_shortcuts
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
