import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.video import utils


def test_has_rpi_camera_stack_with_ffmpeg(monkeypatch):
    monkeypatch.setattr(utils, "has_rpicam_binaries", lambda: False)
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: True)

    assert utils.has_rpi_camera_stack() is True


def test_capture_snapshot_uses_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "has_rpicam_binaries", lambda: False)
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: True)
    monkeypatch.setattr(utils, "RPI_CAMERA_DEVICE", tmp_path / "video0")

    def fake_which(binary):
        if binary == "ffmpeg":
            return "ffmpeg"
        return None

    monkeypatch.setattr(utils.shutil, "which", fake_which)

    captured = {}

    def fake_run(cmd, capture_output, text, check, timeout):  # noqa: ARG001
        captured["cmd"] = cmd
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"frame")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = utils.capture_rpi_snapshot(timeout=1)

    assert result.name.endswith(".jpg")
    assert "ffmpeg" in captured["cmd"][0]
    assert "-frames:v" in captured["cmd"]


def test_capture_snapshot_falls_back_to_ffmpeg(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "has_rpicam_binaries", lambda: True)
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: True)
    monkeypatch.setattr(utils, "RPI_CAMERA_DEVICE", tmp_path / "video0")

    def fake_which(binary):
        if binary == "rpicam-still":
            return "rpicam-still"
        if binary == "ffmpeg":
            return "ffmpeg"
        return None

    monkeypatch.setattr(utils.shutil, "which", fake_which)

    captured: list[list[str]] = []

    def fake_run(cmd, capture_output, text, check, timeout):  # noqa: ARG001
        captured.append(cmd)
        if cmd[0] == "rpicam-still":
            return SimpleNamespace(returncode=1, stdout="", stderr="no cameras available")
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        Path(cmd[-1]).write_bytes(b"frame")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = utils.capture_rpi_snapshot(timeout=1)

    assert result.name.endswith(".jpg")
    assert captured[0][0] == "rpicam-still"
    assert captured[1][0] == "ffmpeg"
