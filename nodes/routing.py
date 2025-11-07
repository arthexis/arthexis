from __future__ import annotations

import re

from django.urls import re_path

from .consumers import AudioCaptureConsumer, AUDIO_CAPTURE_SOCKET_PATH


_audio_pattern = re.escape(AUDIO_CAPTURE_SOCKET_PATH.lstrip("/"))


websocket_urlpatterns = [
    re_path(rf"^{_audio_pattern}$", AudioCaptureConsumer.as_asgi()),
]


__all__ = ["websocket_urlpatterns"]
