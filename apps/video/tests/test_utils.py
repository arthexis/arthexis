from __future__ import annotations

import subprocess
from pathlib import Path

from apps.video import utils

def test_has_rpicam_binaries_rejects_unrunnable_binary(monkeypatch):
    """Binaries on PATH still need to execute successfully."""

    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: f"/usr/bin/{binary}" for binary in utils.RPI_CAMERA_BINARIES},
    )

    def _run(command, **_kwargs):
        if command[0] == "/usr/bin/rpicam-still":
            raise OSError("broken loader")
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", _run)

    assert utils.has_rpicam_binaries() is False

def test_capture_rpi_snapshot_falls_back_when_rpicam_binary_is_unrunnable(
    monkeypatch, tmp_path
):
    """Unrunnable rpicam binaries should not block ffmpeg capture fallback."""

    monkeypatch.setattr(utils, "CAMERA_DIR", tmp_path)
    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: f"/usr/bin/{binary}" for binary in utils.RPI_CAMERA_BINARIES},
    )
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: True)
    monkeypatch.setattr(
        utils.shutil,
        "which",
        lambda binary: f"/usr/bin/{binary}",
    )

    def _run(command, **_kwargs):
        if command == ["/usr/bin/rpicam-hello", "--help"]:
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )
        if command == ["/usr/bin/rpicam-still", "--help"]:
            raise OSError("broken loader")
        if command[0] == "/usr/bin/ffmpeg":
            Path(command[-1]).write_bytes(b"jpeg")
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )
        raise AssertionError(f"unexpected command: {command!r}")

    monkeypatch.setattr(utils.subprocess, "run", _run)

    snapshot = utils.capture_rpi_snapshot()

    assert snapshot.exists()
    assert snapshot.parent == tmp_path

