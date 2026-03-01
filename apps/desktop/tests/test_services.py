"""Regression tests for desktop shortcut synchronization services."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.desktop.models import DesktopShortcut
from apps.desktop.services import (
    render_shortcut_desktop_entry,
    should_install_shortcut,
    sync_desktop_shortcuts,
)
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
    """Unsafe expressions are rejected rather than executed."""

    settings.BASE_DIR = "/home/tester/arthexis"
    shortcut = DesktopShortcut.objects.create(
        slug="unsafe-expression-test",
        desktop_filename="Unsafe Expression Test",
        name="Unsafe Expression Test",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        require_desktop_ui=False,
        condition_expression="has_feature.__globals__['os'].system('id')",
    )

    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode({"user-desktop"}))
    assert should_install_shortcut(shortcut, base_dir=Path(settings.BASE_DIR), username="tester") is False


def test_render_shortcut_desktop_entry_sanitizes_newlines() -> None:
    """Desktop-entry rendering sanitizes newline injection attempts."""

    shortcut = DesktopShortcut(
        slug="newline-test",
        desktop_filename="Newline Test",
        name="Safe\nExec=malicious",
        comment="Comment\r\nTryInject=true",
        launch_mode=DesktopShortcut.LaunchMode.URL,
        target_url="http://127.0.0.1:{port}/",
        categories="Utility;\nNetwork;",
        extra_entries={"X-Test\nInjected": "ok\nnope", "Unsafe=Key": "ignored"},
    )

    rendered = render_shortcut_desktop_entry(
        shortcut,
        exec_value="python -m webbrowser -t http://127.0.0.1:8888/",
        icon_value="applications-system",
    )

    assert "Name=Safe Exec=malicious" in rendered
    assert "Comment=Comment  TryInject=true" in rendered
    assert "Categories=Utility; Network;" in rendered
    assert "X-Test Injected=ok nope" in rendered
    assert "Unsafe=Key" not in rendered


@pytest.mark.django_db
def test_sync_desktop_shortcuts_removes_stale_managed_files(settings, monkeypatch, tmp_path) -> None:
    """Stale managed desktop files are removed even when not prefixed with Arthexis."""

    settings.BASE_DIR = "/home/tester/arthexis"
    desktop_dir = tmp_path / "Desktop"
    desktop_dir.mkdir(parents=True)
    stale = desktop_dir / "Custom Shortcut.desktop"
    stale.write_text("[Desktop Entry]\nX-Arthexis-Managed=true\n", encoding="utf-8")

    monkeypatch.setattr("apps.desktop.services.detect_desktop_dir", lambda *args, **kwargs: desktop_dir)
    monkeypatch.setattr("apps.desktop.services.Node.get_local", lambda: _StubNode(set()))

    result = sync_desktop_shortcuts(base_dir=Path(settings.BASE_DIR), username="tester", port=9000)

    assert not stale.exists()
    assert result.removed == 1
