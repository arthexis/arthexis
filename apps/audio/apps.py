"""Legacy app config forwarding to ``apps.content.audio``."""

from pathlib import Path

from apps.content.audio.apps import AudioConfig as ContentAudioConfig


class AudioConfig(ContentAudioConfig):
    name = "apps.audio"
    path = str(Path(__file__).resolve().parents[1] / "content" / "audio")
