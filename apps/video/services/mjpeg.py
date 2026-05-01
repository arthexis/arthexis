"""Services for MJPEG frame capture and stream body generation."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from django.utils.translation import gettext_lazy as _

from apps.video.services.capture import rotate_cv2_frame
from apps.video.utils import open_cv2_capture


class MjpegDependencyError(RuntimeError):
    """Raised when optional MJPEG streaming dependencies are unavailable."""


class MjpegDeviceUnavailableError(RuntimeError):
    """Raised when the configured MJPEG capture device cannot be opened."""


def load_cv2():
    """Import and return the ``cv2`` module."""

    try:
        import cv2  # type: ignore
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise MjpegDependencyError(
            "MJPEG streaming requires the OpenCV (cv2) package"
        ) from exc
    return cv2


@contextmanager
def _open_capture(*, cv2, device_identifier: str):
    """Create and yield an opened ``cv2.VideoCapture`` instance."""

    capture = open_cv2_capture(cv2, device_identifier)
    if not capture.isOpened():
        capture.release()
        raise MjpegDeviceUnavailableError(
            _("Unable to open video device %(device)s")
            % {"device": device_identifier}
        )
    try:
        yield capture
    finally:  # pragma: no cover
        capture.release()


def iter_device_frame_bytes(*, device_identifier: str, auto_rotate: int):
    """Yield JPEG-encoded bytes from a camera capture device."""

    cv2 = load_cv2()
    with _open_capture(cv2=cv2, device_identifier=device_identifier) as capture:
        while True:
            success, frame = capture.read()
            if not success:
                break
            frame = rotate_cv2_frame(frame, angle=auto_rotate, cv2=cv2)
            success, buffer = cv2.imencode(".jpg", frame)
            if success:
                yield buffer.tobytes()


def capture_device_frame_bytes(*, device_identifier: str, auto_rotate: int) -> bytes | None:
    """Capture one frame from a camera capture device and return JPEG bytes."""

    cv2 = load_cv2()
    with _open_capture(cv2=cv2, device_identifier=device_identifier) as capture:
        success, frame = capture.read()
        if not success:
            return None
        frame = rotate_cv2_frame(frame, angle=auto_rotate, cv2=cv2)
        success, buffer = cv2.imencode(".jpg", frame)
        if not success:
            return None
        return buffer.tobytes()


def encode_mjpeg_chunk(frame_bytes: bytes) -> bytes:
    """Encode one JPEG frame into a multipart MJPEG chunk."""

    return b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"


def stream_mjpeg_bytes(
    *, frame_iter: Iterator[bytes] | None = None, first_frame: bytes | None = None
):
    """Yield multipart MJPEG response chunks from frame bytes."""

    if first_frame is not None:
        yield encode_mjpeg_chunk(first_frame)

    if frame_iter is None:
        return

    for frame in frame_iter:
        yield encode_mjpeg_chunk(frame)
