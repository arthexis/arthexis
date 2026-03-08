"""Unit tests for Pyxel admin view launch helpers."""

from __future__ import annotations

import itertools

import pytest

from apps.pyxel import admin_views


class _EventuallyExitedProcess:
    """Fake process that exits after a configurable number of poll checks."""

    def __init__(self, *, exit_after_polls: int, stderr: str = "display init failed") -> None:
        self._exit_after_polls = exit_after_polls
        self._stderr = stderr
        self._poll_calls = 0

    def poll(self):
        """Return None until the configured poll count, then simulate an exit code."""

        self._poll_calls += 1
        if self._poll_calls >= self._exit_after_polls:
            return 1
        return None

    def communicate(self):
        """Return the configured stderr payload for launch error messaging."""

        return ("", self._stderr)


def test_launch_viewport_subprocess_detects_late_startup_failure(monkeypatch):
    """Regression: launcher should surface process failures during the startup grace window."""

    process = _EventuallyExitedProcess(exit_after_polls=5)
    monotonic_values = itertools.count(0.0, 0.5)

    monkeypatch.setattr(admin_views.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(admin_views.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(admin_views.time, "sleep", lambda _duration: None)

    with pytest.raises(admin_views.PyxelViewportLaunchError, match="display init failed"):
        admin_views.launch_viewport_subprocess(viewport_slug="phone-portrait")


@pytest.mark.parametrize(
    ("platform_name", "display_vars", "expected"),
    [
        pytest.param("linux", {}, False, id="linux-no-display"),
        pytest.param("linux", {"DISPLAY": ":0"}, True, id="linux-with-display"),
        pytest.param("linux", {"WAYLAND_DISPLAY": "wayland-0"}, True, id="linux-with-wayland"),
        pytest.param("win32", {}, True, id="windows"),
        pytest.param("darwin", {}, True, id="macos"),
    ],
)
def test_has_graphical_display(monkeypatch, platform_name, display_vars, expected):
    """Graphical display detection should cover Linux and non-Linux server platforms."""

    monkeypatch.setattr(admin_views.sys, "platform", platform_name)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    for key, value in display_vars.items():
        monkeypatch.setenv(key, value)

    assert admin_views.has_graphical_display() is expected


@pytest.mark.parametrize(
    ("is_wsl_by_release", "is_wsl_by_env", "should_have_wsl_text"),
    [
        pytest.param(False, False, False, id="non-wsl"),
        pytest.param(True, False, True, id="wsl-by-release"),
        pytest.param(False, True, True, id="wsl-by-env"),
    ],
)
def test_viewport_opened_message(monkeypatch, is_wsl_by_release, is_wsl_by_env, should_have_wsl_text):
    """Viewport success text should include WSL guidance only when WSL is detected."""

    monkeypatch.setattr(
        admin_views.platform,
        "release",
        lambda: "5.10.16.3-microsoft-standard-WSL2" if is_wsl_by_release else "5.15.0-48-generic",
    )
    if is_wsl_by_env:
        monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    else:
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)

    message = admin_views.viewport_opened_message("Phone Portrait")

    assert "server desktop" in message
    if should_have_wsl_text:
        assert "In WSL, it appears in the Linux desktop session (WSLg/X11)." in message
    else:
        assert "In WSL" not in message
