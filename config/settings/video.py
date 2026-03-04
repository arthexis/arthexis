"""Video streaming and frame cache settings."""

import json
import os

VIDEO_FRAME_REDIS_URL = os.environ.get("VIDEO_FRAME_REDIS_URL", "").strip()
if not VIDEO_FRAME_REDIS_URL:
    VIDEO_FRAME_REDIS_URL = (
        os.environ.get("CHANNEL_REDIS_URL", "").strip()
        or os.environ.get("CELERY_BROKER_URL", "").strip()
    )
VIDEO_FRAME_CACHE_PREFIX = os.environ.get("VIDEO_FRAME_CACHE_PREFIX", "video:mjpeg")
VIDEO_FRAME_CACHE_TTL = int(os.environ.get("VIDEO_FRAME_CACHE_TTL", "10"))
VIDEO_FRAME_MAX_AGE_SECONDS = int(
    os.environ.get("VIDEO_FRAME_MAX_AGE_SECONDS", "15")
)
VIDEO_FRAME_STREAM_BUFFER_SECONDS = int(
    os.environ.get("VIDEO_FRAME_STREAM_BUFFER_SECONDS", "300")
)
VIDEO_WEBRTC_ICE_SERVERS = []
_webrtc_ice_payload = os.environ.get("VIDEO_WEBRTC_ICE_SERVERS", "").strip()
if _webrtc_ice_payload:
    try:
        parsed = json.loads(_webrtc_ice_payload)
    except (TypeError, ValueError):
        VIDEO_WEBRTC_ICE_SERVERS = []
    else:
        VIDEO_WEBRTC_ICE_SERVERS = parsed if isinstance(parsed, list) else []
VIDEO_FRAME_CAPTURE_INTERVAL = float(
    os.environ.get("VIDEO_FRAME_CAPTURE_INTERVAL", "0.2")
)
VIDEO_FRAME_POLL_INTERVAL = float(os.environ.get("VIDEO_FRAME_POLL_INTERVAL", "0.2"))
VIDEO_FRAME_SERVICE_SLEEP = float(
    os.environ.get("VIDEO_FRAME_SERVICE_SLEEP", "0.05")
)
