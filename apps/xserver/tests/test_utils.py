from __future__ import annotations

from apps.xserver.utils import detect_x_server


def test_detect_x_server_returns_none_without_display(monkeypatch):
    """Detection should return ``None`` when DISPLAY is unavailable."""

    monkeypatch.delenv("DISPLAY", raising=False)

    assert detect_x_server() is None


def test_detect_x_server_detects_local_socket(monkeypatch):
    """Detection should infer a local X server from DISPLAY and unix socket presence."""

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setattr("apps.xserver.utils._run_command", lambda *args: "")
    monkeypatch.setattr("pathlib.Path.exists", lambda self: str(self) == "/tmp/.X11-unix/X0")

    result = detect_x_server()

    assert result is not None
    assert result.display_name == ":0"
    assert result.runtime_scope == "local"
    assert result.server_type == "x11"
