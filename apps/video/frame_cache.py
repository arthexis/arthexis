from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import time
from typing import Iterator

from django.conf import settings
from django.utils import timezone
from redis import Redis
from redis.exceptions import RedisError

from .models import MjpegStream

logger = logging.getLogger(__name__)

_FRAME_REDIS: Redis | None = None


@dataclass(frozen=True)
class CachedFrame:
    frame_bytes: bytes
    frame_id: int | None
    captured_at: datetime | None


def frame_cache_url() -> str:
    """Return the effective Redis URL for the MJPEG frame cache."""
    return getattr(settings, "VIDEO_FRAME_REDIS_URL", "").strip()


def _frame_cache_ttl() -> int:
    return int(getattr(settings, "VIDEO_FRAME_CACHE_TTL", 10) or 10)


def _frame_cache_max_age() -> int:
    return int(getattr(settings, "VIDEO_FRAME_MAX_AGE_SECONDS", 15) or 15)


def _frame_cache_poll_interval() -> float:
    return float(getattr(settings, "VIDEO_FRAME_POLL_INTERVAL", 0.2) or 0.2)


def _frame_capture_interval() -> float:
    return float(getattr(settings, "VIDEO_FRAME_CAPTURE_INTERVAL", 0.2) or 0.2)


def _frame_cache_prefix() -> str:
    return str(getattr(settings, "VIDEO_FRAME_CACHE_PREFIX", "video:mjpeg"))


def _frame_stream_buffer_seconds() -> int:
    return int(getattr(settings, "VIDEO_FRAME_STREAM_BUFFER_SECONDS", 300) or 300)


def get_frame_cache() -> Redis | None:
    global _FRAME_REDIS
    if _FRAME_REDIS is not None:
        return _FRAME_REDIS
    url = frame_cache_url()
    if not url:
        return None
    try:
        _FRAME_REDIS = Redis.from_url(
            url,
            decode_responses=False,
            socket_timeout=1,
            socket_connect_timeout=1,
        )
    except Exception as exc:  # pragma: no cover - runtime dependency
        logger.warning("Unable to connect to frame cache redis: %s", exc)
        _FRAME_REDIS = None
    return _FRAME_REDIS


def _cache_key(stream: MjpegStream, suffix: str) -> str:
    return f"{_frame_cache_prefix()}:{stream.slug}:{suffix}"


def _stream_key(stream: MjpegStream) -> str:
    return _cache_key(stream, "stream")


def store_frame(stream: MjpegStream, frame_bytes: bytes) -> CachedFrame | None:
    client = get_frame_cache()
    if not client:
        return None
    ttl = _frame_cache_ttl()
    captured_at = timezone.now()
    frame_key = _cache_key(stream, "frame")
    ts_key = _cache_key(stream, "captured_at")
    id_key = _cache_key(stream, "frame_id")
    stream_key = _stream_key(stream)
    buffer_seconds = max(_frame_stream_buffer_seconds(), 1)
    capture_interval = max(_frame_capture_interval(), 0.01)
    try:
        pipeline = client.pipeline()
        pipeline.incr(id_key)
        pipeline.set(frame_key, frame_bytes, ex=ttl)
        pipeline.set(ts_key, captured_at.isoformat(), ex=ttl)
        pipeline.expire(id_key, ttl)
        maxlen = max(int(buffer_seconds / capture_interval) + 1, 1)
        pipeline.xadd(
            stream_key,
            {
                "frame": frame_bytes,
                "captured_at": captured_at.isoformat(),
            },
            maxlen=maxlen,
            approximate=True,
        )
        pipeline.expire(stream_key, buffer_seconds)
        results = pipeline.execute()
    except RedisError as exc:  # pragma: no cover - runtime dependency
        logger.warning("Unable to store MJPEG frame for %s: %s", stream.slug, exc)
        return None
    frame_id = int(results[0]) if results else None
    return CachedFrame(frame_bytes=frame_bytes, frame_id=frame_id, captured_at=captured_at)


def store_status(stream: MjpegStream, payload: dict[str, object]) -> None:
    client = get_frame_cache()
    if not client:
        return
    ttl = _frame_cache_ttl()
    status_key = _cache_key(stream, "status")
    body = json.dumps(payload, sort_keys=True)
    try:
        client.set(status_key, body, ex=ttl)
    except RedisError as exc:  # pragma: no cover - runtime dependency
        logger.debug("Unable to store MJPEG status for %s: %s", stream.slug, exc)


def get_frame(stream: MjpegStream) -> CachedFrame | None:
    client = get_frame_cache()
    if not client:
        return None
    frame_key = _cache_key(stream, "frame")
    ts_key = _cache_key(stream, "captured_at")
    id_key = _cache_key(stream, "frame_id")
    try:
        frame_bytes, ts_bytes, frame_id_bytes = client.mget(
            [frame_key, ts_key, id_key]
        )
    except RedisError as exc:  # pragma: no cover - runtime dependency
        logger.warning("Unable to read MJPEG frame for %s: %s", stream.slug, exc)
        return None
    if not frame_bytes:
        return None
    frame_id = None
    if frame_id_bytes:
        try:
            frame_id = int(frame_id_bytes.decode("utf-8"))
        except (TypeError, ValueError, AttributeError):
            frame_id = None
    captured_at = None
    if ts_bytes:
        try:
            captured_at = datetime.fromisoformat(ts_bytes.decode("utf-8"))
            if timezone.is_naive(captured_at):
                captured_at = timezone.make_aware(captured_at)
        except (TypeError, ValueError, AttributeError):
            captured_at = None
    if captured_at:
        age = (timezone.now() - captured_at).total_seconds()
        if age > _frame_cache_max_age():
            return None
    return CachedFrame(frame_bytes=frame_bytes, frame_id=frame_id, captured_at=captured_at)


def get_status(stream: MjpegStream) -> dict[str, object] | None:
    client = get_frame_cache()
    if not client:
        return None
    status_key = _cache_key(stream, "status")
    try:
        status_body = client.get(status_key)
    except RedisError as exc:  # pragma: no cover - runtime dependency
        logger.debug("Unable to read MJPEG status for %s: %s", stream.slug, exc)
        return None
    if not status_body:
        return None
    try:
        payload = json.loads(status_body)
    except (TypeError, ValueError):  # pragma: no cover - bad status payload
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def mjpeg_frame_stream(
    stream: MjpegStream,
    *,
    first_frame: CachedFrame,
) -> Iterator[bytes]:
    boundary = b"--frame\r\n"
    content_type = b"Content-Type: image/jpeg\r\n\r\n"
    last_id = first_frame.frame_id
    yield boundary + content_type + first_frame.frame_bytes + b"\r\n"
    while True:
        cached = get_frame(stream)
        if cached and cached.frame_bytes:
            if cached.frame_id is None or cached.frame_id != last_id:
                last_id = cached.frame_id
                yield boundary + content_type + cached.frame_bytes + b"\r\n"
        time.sleep(_frame_cache_poll_interval())
