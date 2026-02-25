"""Video app model exports.

This package preserves compatibility with imports such as
``from apps.video.models import VideoDevice``.
"""

from apps.video.models.device import DetectedVideoDevice, VideoDevice
from apps.video.models.recording import VideoRecording
from apps.video.models.snapshot import VideoSnapshot
from apps.video.models.stream import (
    MjpegDependencyError,
    MjpegDeviceUnavailableError,
    MjpegStream,
    VideoStream,
)
from apps.video.models.youtube import YoutubeChannel

__all__ = [
    "DetectedVideoDevice",
    "MjpegDependencyError",
    "MjpegDeviceUnavailableError",
    "MjpegStream",
    "VideoDevice",
    "VideoRecording",
    "VideoSnapshot",
    "VideoStream",
    "YoutubeChannel",
]
