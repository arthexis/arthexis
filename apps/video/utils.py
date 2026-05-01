import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from django.conf import settings

logger = logging.getLogger(__name__)

WORK_DIR = Path(settings.BASE_DIR) / "work"
CAMERA_DIR = WORK_DIR / "camera"
RPI_CAMERA_DEVICE = Path("/dev/video0")
RPI_CAMERA_BINARIES = ("rpicam-hello", "rpicam-still", "rpicam-vid")
DEFAULT_CAMERA_RESOLUTION = (1280, 720)
OPENCV_CAMERA_IDENTIFIER_PREFIX = "opencv:"
FALLBACK_CAMERA_RESOLUTIONS = (
    (1920, 1080),
    (1280, 720),
    (1024, 768),
    (800, 600),
    (640, 480),
)

_CAMERA_LOCK = threading.Lock()


@dataclass(frozen=True)
class CameraStackProbe:
    """Summarize camera-stack availability and the first useful reason."""

    available: bool
    backend: str
    reason: str
    detected_cameras: int = 0


class Cv2CameraDevice(NamedTuple):
    """OpenCV-discovered video capture device."""

    identifier: str
    description: str
    raw_info: str


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


def _rpicam_binary_paths() -> dict[str, str | None]:
    """Return resolved paths for Raspberry Pi camera binaries."""

    return {binary: shutil.which(binary) for binary in RPI_CAMERA_BINARIES}


def _parse_rpicam_camera_count(output: str) -> int:
    """Return the number of attached cameras listed by ``--list-cameras``."""

    count = 0
    for line in output.splitlines():
        if re.match(r"^\s*\d+\s*:", line):
            count += 1
    return count


def _list_rpicam_cameras(timeout: int = 5) -> tuple[int, str, str]:
    """Return attached-camera count, the best probe message, and raw output."""

    binary_paths = _rpicam_binary_paths()
    tool_path = binary_paths.get("rpicam-hello") or binary_paths.get("rpicam-still")
    if not tool_path:
        return (0, "rpicam-hello is not available", "")

    try:
        result = subprocess.run(
            [tool_path, "--list-cameras"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover
        return (0, f"Unable to list cameras: {exc}", "")

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0:
        return (0, output or "Unable to list cameras", output)
    if not output:
        return (0, "No camera information returned", "")
    if "No cameras available" in output:
        return (0, "No attached cameras detected", output)

    camera_count = _parse_rpicam_camera_count(output)
    if camera_count > 0:
        suffix = "camera" if camera_count == 1 else "cameras"
        return (camera_count, f"{camera_count} attached {suffix} detected", output)
    return (0, "Unable to determine attached cameras", output)


def has_rpicam_binaries() -> bool:
    """Return ``True`` when the Raspberry Pi camera binaries are available."""

    for tool_path in _rpicam_binary_paths().values():
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
        except (OSError, subprocess.SubprocessError):
            return False
        if result.returncode != 0:
            return False
    return True


def _has_ffmpeg_capture_support() -> bool:
    """Return ``True`` when a generic V4L2 device can be captured with ffmpeg."""

    if not _is_video_device_available(RPI_CAMERA_DEVICE):
        return False
    return shutil.which("ffmpeg") is not None


def probe_rpi_camera_stack(timeout: int = 5) -> CameraStackProbe:
    """Return whether a usable camera stack is available and why."""

    rpicam_available = has_rpicam_binaries()
    rpicam_reason = ""
    if rpicam_available:
        camera_count, rpicam_reason, _output = _list_rpicam_cameras(timeout=timeout)
        if camera_count > 0:
            return CameraStackProbe(
                available=True,
                backend="rpicam",
                reason=rpicam_reason,
                detected_cameras=camera_count,
            )

    if _has_ffmpeg_capture_support():
        return CameraStackProbe(
            available=True,
            backend="ffmpeg",
            reason=f"Video4Linux device {RPI_CAMERA_DEVICE} is available",
        )

    if rpicam_available:
        return CameraStackProbe(
            available=False,
            backend="missing",
            reason=rpicam_reason,
        )

    missing_binaries = [
        binary for binary, path in _rpicam_binary_paths().items() if not path
    ]
    reasons: list[str] = []
    if missing_binaries:
        reasons.append(f"missing rpicam binaries: {', '.join(missing_binaries)}")
    if shutil.which("ffmpeg") is None:
        reasons.append("ffmpeg is unavailable")
    if not _is_video_device_available(RPI_CAMERA_DEVICE):
        reasons.append(f"{RPI_CAMERA_DEVICE} is unavailable")
    reason = "; ".join(reasons) or "No supported camera stack is available"
    return CameraStackProbe(available=False, backend="missing", reason=reason)


def has_rpi_camera_stack() -> bool:
    """Return ``True`` when any supported camera stack is available."""

    return probe_rpi_camera_stack().available


def cv2_camera_identifier(index: int) -> str:
    """Return a non-ambiguous identifier for an OpenCV camera index."""

    return f"{OPENCV_CAMERA_IDENTIFIER_PREFIX}{int(index)}"


def _cv2_camera_index(device_identifier: str | int) -> int | None:
    """Return an OpenCV camera index parsed from a supported identifier."""

    if type(device_identifier) is int:
        return device_identifier
    identifier = str(device_identifier or "").strip()
    if identifier.startswith(OPENCV_CAMERA_IDENTIFIER_PREFIX):
        suffix = identifier[len(OPENCV_CAMERA_IDENTIFIER_PREFIX) :].strip()
        return int(suffix) if suffix.isdigit() else None
    return int(identifier) if identifier.isdigit() else None


def open_cv2_capture(cv2, device_identifier: str | int):
    """Open an OpenCV capture source using the best local backend."""

    index = _cv2_camera_index(device_identifier)
    if index is not None:
        if os.name == "nt" and hasattr(cv2, "CAP_DSHOW"):
            return cv2.VideoCapture(index, cv2.CAP_DSHOW)
        return cv2.VideoCapture(index)
    return cv2.VideoCapture(str(device_identifier or "").strip())


def detect_cv2_camera_devices(limit: int | None = None) -> list[Cv2CameraDevice]:
    """Return camera devices that OpenCV can open locally."""

    try:
        import cv2  # type: ignore
    except ImportError:
        return []

    probe_limit = limit
    if probe_limit is None:
        probe_limit = int(getattr(settings, "VIDEO_CV2_DISCOVERY_LIMIT", 5))

    detected: list[Cv2CameraDevice] = []
    for index in range(max(0, probe_limit)):
        identifier = cv2_camera_identifier(index)
        capture = open_cv2_capture(cv2, identifier)
        try:
            if not capture.isOpened():
                continue
            success, frame = capture.read()
            if not success or frame is None:
                continue
            height, width = frame.shape[:2]
            backend = (
                capture.getBackendName()
                if hasattr(capture, "getBackendName")
                else "opencv"
            )
            detected.append(
                Cv2CameraDevice(
                    identifier=identifier,
                    description=f"OpenCV Camera {index}",
                    raw_info=(
                        f"device_index={index} backend={backend} "
                        f"frame_size={width}x{height}"
                    ),
                )
            )
        finally:
            capture.release()
    return detected


def capture_cv2_snapshot(
    device_identifier: str,
    *,
    timeout: int = 10,
    width: int | None = None,
    height: int | None = None,
    auto_rotate: int = 0,
) -> Path:
    """Capture a JPEG snapshot from an OpenCV-supported video device."""

    from apps.video.services.capture import rotate_cv2_frame
    from apps.video.services.mjpeg import load_cv2

    cv2 = load_cv2()
    identifier = str(device_identifier or "").strip()
    acquired = _CAMERA_LOCK.acquire(timeout=timeout)
    if not acquired:
        raise RuntimeError("Camera is busy. Wait for the current capture to finish.")

    capture = open_cv2_capture(cv2, identifier)
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Unable to open video device {device_identifier}")
        if width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
        if height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))
        success, frame = capture.read()
        if not success or frame is None:
            raise RuntimeError(
                f"Unable to capture frame from video device {device_identifier}"
            )
        frame = rotate_cv2_frame(frame, angle=auto_rotate, cv2=cv2)
        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            raise RuntimeError("JPEG encode failed")
    finally:
        capture.release()
        _CAMERA_LOCK.release()

    CAMERA_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc)
    filename = CAMERA_DIR / f"{timestamp:%Y%m%d%H%M%S}-{uuid.uuid4().hex}.jpg"
    filename.write_bytes(buffer.tobytes())
    return filename


def get_camera_resolutions() -> list[tuple[int, int]]:
    """Return supported camera resolutions when available."""

    camera_count, _reason, output = _list_rpicam_cameras()
    if camera_count <= 0:
        return list(FALLBACK_CAMERA_RESOLUTIONS)

    resolutions: set[tuple[int, int]] = set()
    for line in output.splitlines():
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
    "CameraStackProbe",
    "CAMERA_DIR",
    "DEFAULT_CAMERA_RESOLUTION",
    "FALLBACK_CAMERA_RESOLUTIONS",
    "OPENCV_CAMERA_IDENTIFIER_PREFIX",
    "RPI_CAMERA_BINARIES",
    "RPI_CAMERA_DEVICE",
    "has_rpicam_binaries",
    "capture_rpi_snapshot",
    "cv2_camera_identifier",
    "get_camera_resolutions",
    "has_rpi_camera_stack",
    "capture_cv2_snapshot",
    "detect_cv2_camera_devices",
    "open_cv2_capture",
    "probe_rpi_camera_stack",
    "record_rpi_video",
]
