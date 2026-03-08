"""Tests for desktop-launch capability resolution and command output."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management import call_command

from apps.desktop.services import get_desktop_launch_capabilities


class _StubNode:
    """Simple node stub exposing ``has_feature`` for capability tests."""

    def __init__(self, features: set[str]):
        self._features = features

    def has_feature(self, slug: str) -> bool:
        """Return whether this stub declares the requested feature slug."""

        return slug in self._features


def test_get_desktop_launch_capabilities_reads_feature_backed_state(
    monkeypatch, tmp_path
):
    """Desktop-launch capability output should be sourced from node feature state."""

    base_dir = Path(tmp_path)
    locks_dir = base_dir / ".locks"
    locks_dir.mkdir()
    (locks_dir / "backend_port.lck").write_text("9123", encoding="utf-8")
    (locks_dir / "service.lck").write_text("arthexis-local", encoding="utf-8")

    monkeypatch.setattr(
        "apps.desktop.services.Node.get_local",
        lambda: _StubNode({"user-desktop", "systemd-manager"}),
    )
    monkeypatch.setattr("apps.desktop.services.is_desktop_ui_available", lambda: True)
    monkeypatch.setattr(
        "apps.desktop.services.resolve_browser_opener", lambda: "xdg-open"
    )

    capabilities = get_desktop_launch_capabilities(base_dir=base_dir)

    assert capabilities.desktop_context_ready is True
    assert capabilities.systemd_control_available is True
    assert capabilities.browser_opener_available is True
    assert capabilities.browser_opener_command == "xdg-open"
    assert capabilities.backend_port == 9123
    assert capabilities.service_name == "arthexis-local"
    assert capabilities.metadata_available is True


def test_get_desktop_launch_prereq_state_reports_three_prerequisites(monkeypatch):
    """Desktop node feature hooks should expose canonical launch prerequisites."""

    from apps.desktop.node_features import get_desktop_launch_prereq_state

    monkeypatch.setattr(
        "apps.desktop.node_features.is_desktop_ui_available", lambda: True
    )
    monkeypatch.setattr(
        "apps.desktop.node_features._systemctl_command", lambda: ["systemctl"]
    )
    monkeypatch.setattr(
        "apps.desktop.node_features.resolve_browser_opener", lambda: "xdg-open"
    )

    prereqs = get_desktop_launch_prereq_state(base_dir=Path("."), base_path=Path("."))

    assert prereqs == {
        "desktop_context_ready": True,
        "systemd_control_available": True,
        "browser_opener_available": True,
    }


def test_desktop_launch_capabilities_command_prints_json(monkeypatch, tmp_path, capsys):
    """Management command should print serialized desktop-launch capabilities."""

    monkeypatch.setattr(
        "apps.desktop.services.Node.get_local",
        lambda: _StubNode({"user-desktop"}),
    )
    monkeypatch.setattr("apps.desktop.services.is_desktop_ui_available", lambda: True)
    monkeypatch.setattr(
        "apps.desktop.services.resolve_browser_opener", lambda: "firefox"
    )

    call_command("desktop_launch_capabilities", base_dir=str(tmp_path))

    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload["desktop_context_ready"] is True
    assert payload["systemd_control_available"] is False
    assert payload["browser_opener_available"] is True
    assert payload["browser_opener_command"] == "firefox"
    assert payload["metadata_available"] is True
