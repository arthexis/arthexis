"""Unit tests for CLI Pyxel command routing."""

from __future__ import annotations

import pytest

from apps.pyxel.management.commands import pyxel as pyxel_command


def test_pyxel_command_routes_to_viewport(monkeypatch):
    """Default CLI invocation should forward to the saved viewport command."""

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        pyxel_command,
        "call_command",
        lambda name, *args, **kwargs: calls.append((name, args)),
    )

    pyxel_command.Command().handle(viewport="phone_portrait", live_stats=False)

    assert calls == [("viewport", ("phone_portrait",))]


def test_pyxel_command_routes_to_live_stats(monkeypatch):
    """The explicit live-stats flag should launch the live-stats viewport command."""

    calls: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(
        pyxel_command,
        "call_command",
        lambda name, *args, **kwargs: calls.append((name, args)),
    )

    pyxel_command.Command().handle(viewport="", live_stats=True)

    assert calls == [("live_stats_viewport", ())]


def test_pyxel_command_rejects_conflicting_modes():
    """Viewport selection and live-stats mode are mutually exclusive."""

    with pytest.raises(pyxel_command.CommandError, match="Choose either"):
        pyxel_command.Command().handle(viewport="phone_portrait", live_stats=True)
