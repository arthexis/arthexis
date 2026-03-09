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


