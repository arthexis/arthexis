import logging
import os
import shutil
import stat
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

WORK_DIR = Path(settings.BASE_DIR) / "work"
CAMERA_DIR = WORK_DIR / "camera"
RPI_CAMERA_DEVICE = Path("/dev/video0")
RPI_CAMERA_BINARIES = ("rpicam-hello", "rpicam-still", "rpicam-vid")
DEFAULT_CAMERA_RESOLUTION = (1280, 720)
FALLBACK_CAMERA_RESOLUTIONS = (
    (1920, 1080),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
)

_CAMERA_LOCK = threading.Lock()


def _is_video_device_available(device: Path) -> bool:
    """Return ``True`` when ``device`` exists and is a readable char device."""

    device_path = str(device)
    try:
        mode = os.stat(device_path).st_mode
    except OSError:
        return False
    if not stat.S_ISCHR(mode):
        return False
    if not os.access(device_path, os.R_OK | os.W_OK):
        return False
    return True


def has_rpicam_binaries() -> bool:
    """Return ``True`` when the Raspberry Pi camera binaries are available."""

    device = RPI_CAMERA_DEVICE
    if not _is_video_device_available(device):
        return False
    for binary in RPI_CAMERA_BINARIES:
        tool_path = shutil.which(binary)
        if not tool_path:
            return False
        try:
            result = subprocess.run(
                [tool_path, "--help"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except Exception:
            return False
        if result.returncode != 0:
            return False
    return True


def _has_ffmpeg_capture_support() -> bool:
    """Return ``True`` when a generic V4L2 device can be captured with ffmpeg."""

    if not _is_video_device_available(RPI_CAMERA_DEVICE):
        return False
    return shutil.which("ffmpeg") is not None


def has_rpi_camera_stack() -> bool:
    """Return ``True`` when any supported camera stack is available."""

    return has_rpicam_binaries() or _has_ffmpeg_capture_support()


def get_camera_resolutions() -> list[tuple[int, int]]:
    """Return supported camera resolutions when available."""

    if not has_rpi_camera_stack():
        return list(FALLBACK_CAMERA_RESOLUTIONS)

    tool_path = shutil.which("rpicam-hello")
    if not tool_path:
        return list(FALLBACK_CAMERA_RESOLUTIONS)

    try:
        result = subprocess.run(
            [tool_path, "--list-cameras"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        return list(FALLBACK_CAMERA_RESOLUTIONS)

    if result.returncode != 0:
        return list(FALLBACK_CAMERA_RESOLUTIONS)

    resolutions: set[tuple[int, int]] = set()
    for line in result.stdout.splitlines():
        if "x" not in line:
            continue
        for chunk in line.split():
            if "x" not in chunk:
                continue
            candidate = chunk.strip(",")
            parts = candidate.lower().split("x")
            if len(parts) != 2:
                continue
            try:
                width = int(parts[0])
                height = int(parts[1])
            except ValueError:
                continue
            if width > 0 and height > 0:
                resolutions.add((width, height))

    if not resolutions:
        return list(FALLBACK_CAMERA_RESOLUTIONS)
    return sorted(resolutions, reverse=True)


def capture_rpi_snapshot(
    timeout: int = 10,
    *,
    width: int | None = None,
    height: int | None = None,
) -> Path:
    """Capture a snapshot using the Raspberry Pi camera stack."""

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.jpg"
    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    def _build_ffmpeg_command() -> tuple[list[str], str]:
        tool_path = shutil.which("ffmpeg")
        if not tool_path:
            raise RuntimeError("ffmpeg is not available")
        command = [
            tool_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-i",
            str(RPI_CAMERA_DEVICE),
        ]
        if width and height:
            command.extend(["-video_size", f"{width}x{height}"])
        command.extend(["-frames:v", "1", "-y", str(filename)])
        return (command, tool_path)

    def _run_command(command: list[str], tool_path: str) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except Exception as exc:  # pragma: no cover - depends on camera stack
            logger.error("Failed to invoke %s: %s", tool_path, exc)
            raise RuntimeError(f"Snapshot capture failed: {exc}") from exc

    try:
        result: subprocess.CompletedProcess | None = None

        if has_rpicam_binaries():
            tool_path = shutil.which("rpicam-still")
            if not tool_path:
                raise RuntimeError("rpicam-still is not available")

            command = [tool_path, "-o", str(filename), "-t", "1"]
            if width and height:
                command.extend(["--width", str(width), "--height", str(height)])
            result = _run_command(command, tool_path)
            if result.returncode != 0 and _has_ffmpeg_capture_support():
                error = (result.stderr or result.stdout or "Snapshot capture failed").strip()
                logger.warning(
                    "rpicam-still failed (%s); attempting ffmpeg fallback", error
                )
                result = None

        if result is None:
            if _has_ffmpeg_capture_support():
                command, tool_path = _build_ffmpeg_command()
                result = _run_command(command, tool_path)
            else:
                raise RuntimeError("No supported camera stack is available")
    finally:
        _CAMERA_LOCK.release()

    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Snapshot capture failed").strip()
        logger.error("Snapshot command exited with %s: %s", result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Snapshot file %s was not created", filename)
        raise RuntimeError("Snapshot capture failed")
    return filename


def record_rpi_video(duration_seconds: int = 5, timeout: int = 15) -> Path:
    """Record a short video using the Raspberry Pi camera stack."""

    tool_path = shutil.which("rpicam-vid")
    if not tool_path:
        raise RuntimeError("rpicam-vid is not available")

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow()
    unique_suffix = uuid.uuid4().hex
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{unique_suffix}.mp4"

    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    try:
        result = subprocess.run(
            [
                tool_path,
                "-o",
                str(filename),
                "-t",
                str(max(1, duration_seconds * 1000)),
                "--codec",
                "libav",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except Exception as exc:  # pragma: no cover - depends on camera stack
        logger.error("Failed to invoke %s: %s", tool_path, exc)
        raise RuntimeError(f"Video capture failed: {exc}") from exc
    finally:
        _CAMERA_LOCK.release()
    if result.returncode != 0:
        error = (result.stderr or result.stdout or "Video capture failed").strip()
        logger.error("rpicam-vid exited with %s: %s", result.returncode, error)
        raise RuntimeError(error)
    if not filename.exists():
        logger.error("Video file %s was not created", filename)
        raise RuntimeError("Video capture failed")
    return filename


__all__ = [
    "CAMERA_DIR",
    "DEFAULT_CAMERA_RESOLUTION",
    "FALLBACK_CAMERA_RESOLUTIONS",
    "RPI_CAMERA_BINARIES",
    "RPI_CAMERA_DEVICE",
    "has_rpicam_binaries",
    "capture_rpi_snapshot",
    "get_camera_resolutions",
    "has_rpi_camera_stack",
    "record_rpi_video",
]
