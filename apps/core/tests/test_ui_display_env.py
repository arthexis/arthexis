from __future__ import annotations

from apps.core import ui


def test_recommended_graphical_env_repairs_wslg_runtime(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime-dir"
    runtime_dir.mkdir(parents=True)
    (runtime_dir / "wayland-0").write_text("", encoding="utf-8")
    x11_dir = tmp_path / "x11"
    x11_dir.mkdir(parents=True)
    (x11_dir / "X0").write_text("", encoding="utf-8")
    pulse_server = tmp_path / "PulseServer"
    pulse_server.write_text("", encoding="utf-8")

    monkeypatch.setattr(ui, "WSLG_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(ui, "WSLG_PULSE_SERVER", pulse_server)
    monkeypatch.setattr(ui, "X11_SOCKET_DIRS", (x11_dir,))
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 6.6.0-microsoft-standard-WSL2", encoding="utf-8")
    monkeypatch.setattr(ui, "WSL_PROC_VERSION_PATH", proc_version)

    env = {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": "/run/user/1000",
    }

    recommended = ui.recommended_graphical_env(env)

    assert recommended == {
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "PULSE_SERVER": f"unix:{pulse_server}",
    }
    assert ui.has_graphical_display(env) is True


def test_has_graphical_display_false_without_display_config(monkeypatch, tmp_path):
    proc_version = tmp_path / "proc_version"
    proc_version.write_text("Linux version 6.6.0", encoding="utf-8")
    monkeypatch.setattr(ui, "WSL_PROC_VERSION_PATH", proc_version)
    monkeypatch.setattr(ui, "WSLG_RUNTIME_DIR", tmp_path / "missing-runtime")
    monkeypatch.setattr(ui, "WSLG_PULSE_SERVER", tmp_path / "missing-pulse")
    monkeypatch.setattr(ui, "X11_SOCKET_DIRS", (tmp_path / "missing-x11",))

    assert ui.has_graphical_display({}) is False
