"""Legacy app config forwarding to ``apps.content.storage``."""

from pathlib import Path

from apps.content.storage.apps import MediaConfig as ContentMediaConfig


class MediaConfig(ContentMediaConfig):
    name = "apps.media"
    path = str(Path(__file__).resolve().parents[1] / "content" / "storage")
