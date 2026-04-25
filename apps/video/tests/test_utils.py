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


def test_probe_rpi_camera_stack_reports_disconnected_rpicam(monkeypatch):
    """The probe should report disconnected cameras before capture is attempted."""

    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: f"/usr/bin/{binary}" for binary in utils.RPI_CAMERA_BINARIES},
    )
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: False)

    def _run(command, **_kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )
        assert command[-1] == "--list-cameras"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout="No cameras available!\n",
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", _run)

    probe = utils.probe_rpi_camera_stack()

    assert probe.available is False
    assert probe.backend == "missing"
    assert probe.reason == "No attached cameras detected"


def test_probe_rpi_camera_stack_reports_attached_rpicam(monkeypatch):
    """A listed rpicam sensor should produce an available rpicam probe."""

    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: f"/usr/bin/{binary}" for binary in utils.RPI_CAMERA_BINARIES},
    )
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: False)

    def _run(command, **_kwargs):
        if command[-1] == "--help":
            return subprocess.CompletedProcess(
                args=command,
                returncode=0,
                stdout="",
                stderr="",
            )
        assert command[-1] == "--list-cameras"
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Available cameras\n"
                "0 : imx708 [4608x2592]\n"
                "1 : ov5647 [2592x1944]\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", _run)

    probe = utils.probe_rpi_camera_stack()

    assert probe.available is True
    assert probe.backend == "rpicam"
    assert probe.detected_cameras == 2
    assert probe.reason == "2 attached cameras detected"


def test_probe_rpi_camera_stack_uses_ffmpeg_fallback(monkeypatch):
    """A live V4L2 device should still count as an available camera stack."""

    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: None for binary in utils.RPI_CAMERA_BINARIES},
    )
    monkeypatch.setattr(utils, "_has_ffmpeg_capture_support", lambda: True)

    probe = utils.probe_rpi_camera_stack()

    assert probe.available is True
    assert probe.backend == "ffmpeg"
    assert str(utils.RPI_CAMERA_DEVICE) in probe.reason


def test_get_camera_resolutions_reuses_single_rpicam_probe(monkeypatch):
    """Resolution discovery should not run a second camera-list subprocess."""

    calls = []
    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {binary: f"/usr/bin/{binary}" for binary in utils.RPI_CAMERA_BINARIES},
    )

    def _run(command, **_kwargs):
        calls.append(command)
        assert command == ["/usr/bin/rpicam-hello", "--list-cameras"]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Available cameras\n"
                "0 : imx708 [4608x2592]\n"
                "    Modes: 'SRGGB10_CSI2P' : 2304x1296 1536x864\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", _run)

    assert utils.get_camera_resolutions() == [(2304, 1296), (1536, 864)]
    assert len(calls) == 1


def test_get_camera_resolutions_uses_rpicam_still_fallback(monkeypatch):
    """Resolution discovery should match rpicam probe binary fallback behavior."""

    monkeypatch.setattr(
        utils,
        "_rpicam_binary_paths",
        lambda: {
            "rpicam-hello": None,
            "rpicam-still": "/usr/bin/rpicam-still",
            "rpicam-vid": "/usr/bin/rpicam-vid",
        },
    )

    def _run(command, **_kwargs):
        assert command == ["/usr/bin/rpicam-still", "--list-cameras"]
        return subprocess.CompletedProcess(
            args=command,
            returncode=0,
            stdout=(
                "Available cameras\n"
                "0 : imx708 [4608x2592]\n"
                "    Modes: 'SRGGB10_CSI2P' : 1920x1080 1280x720\n"
            ),
            stderr="",
        )

    monkeypatch.setattr(utils.subprocess, "run", _run)

    assert utils.get_camera_resolutions() == [(1920, 1080), (1280, 720)]
