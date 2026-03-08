"""Legacy app config forwarding to ``apps.content.video``."""

from pathlib import Path

from apps.content.video.apps import VideoConfig as ContentVideoConfig


class VideoConfig(ContentVideoConfig):
    name = "apps.video"
    path = str(Path(__file__).resolve().parents[1] / "content" / "video")
