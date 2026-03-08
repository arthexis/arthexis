"""Video app model exports.

This package preserves compatibility with imports such as
``from apps.content.video.models import VideoDevice``.
"""

from apps.content.video.models.device import DetectedVideoDevice, VideoDevice
from apps.content.video.models.recording import VideoRecording
from apps.content.video.models.snapshot import VideoSnapshot
from apps.content.video.models.stream import (
    MjpegDependencyError,
    MjpegDeviceUnavailableError,
    MjpegStream,
    VideoStream,
)
from apps.content.video.models.youtube import YoutubeChannel

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
