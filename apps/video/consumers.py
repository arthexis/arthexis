from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import av
import cv2  # type: ignore
import numpy as np  # type: ignore
from aiortc import RTCPeerConnection, RTCIceCandidate, RTCSessionDescription
from aiortc.mediastreams import VideoStreamTrack
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer, AsyncWebsocketConsumer
from django.conf import settings
from redis.asyncio import Redis
from redis.exceptions import RedisError

from .frame_cache import get_frame, get_status
from .models import MjpegStream

logger = logging.getLogger(__name__)


@database_sync_to_async
def _get_stream(slug: str, *, include_inactive: bool) -> MjpegStream | None:
    queryset = MjpegStream.objects.select_related("video_device")
    if not include_inactive:
        queryset = queryset.filter(is_active=True)
    return queryset.filter(slug=slug).first()


def _stream_key(slug: str) -> str:
    prefix = str(getattr(settings, "VIDEO_FRAME_CACHE_PREFIX", "video:mjpeg"))
    return f"{prefix}:{slug}:stream"


def _redis_url() -> str:
    return getattr(settings, "VIDEO_FRAME_REDIS_URL", "").strip()


def _parse_query_string(scope: dict[str, Any]) -> dict[str, str]:
    raw = scope.get("query_string", b"")
    if not raw:
        return {}
    text = raw.decode("utf-8", errors="ignore")
    pairs = [part.split("=", 1) for part in text.split("&") if part]
    parsed: dict[str, str] = {}
    for pair in pairs:
        key = pair[0]
        value = pair[1] if len(pair) > 1 else ""
        parsed[key] = value
    return parsed


def _resolve_start_id(scope: dict[str, Any]) -> str:
    params = _parse_query_string(scope)
    since = params.get("since")
    if since:
        return since
    replay = params.get("replay")
    if replay and replay.lower() in {"1", "true", "yes"}:
        return "0-0"
    return "$"


class RedisVideoStreamTrack(VideoStreamTrack):
    kind = "video"

    def __init__(self, slug: str, *, start_id: str) -> None:
        super().__init__()
        self._slug = slug
        self._stream_key = _stream_key(slug)
        self._redis = Redis.from_url(_redis_url(), decode_responses=False)
        self._last_id = start_id
        self._closed = False

    async def recv(self) -> av.VideoFrame:
        while True:
            if self._closed:
                raise asyncio.CancelledError()
            try:
                entries = await self._redis.xread(
                    {self._stream_key: self._last_id},
                    block=1000,
                    count=1,
                )
            except RedisError as exc:
                logger.warning("WebRTC redis read failed for %s: %s", self._slug, exc)
                await asyncio.sleep(0.2)
                continue
            if not entries:
                continue
            _, items = entries[0]
            for entry_id, fields in items:
                self._last_id = entry_id
                frame_bytes = fields.get(b"frame")
                if not frame_bytes:
                    continue
                frame = self._decode_frame(frame_bytes)
                if frame is None:
                    continue
                frame.pts, frame.time_base = await self.next_timestamp()
                return frame

    def _decode_frame(self, frame_bytes: bytes) -> av.VideoFrame | None:
        try:
            data = np.frombuffer(frame_bytes, dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
            if image is None:
                return None
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            return av.VideoFrame.from_ndarray(rgb, format="rgb24")
        except Exception as exc:  # pragma: no cover - best-effort decode
            logger.debug("Unable to decode frame for %s: %s", self._slug, exc)
            return None

    async def stop(self) -> None:
        self._closed = True
        await self._redis.close()
        await super().stop()


class RedisStreamConsumer(AsyncWebsocketConsumer):
    async def connect(self) -> None:
        slug = self.scope["url_route"]["kwargs"]["slug"]
        include_inactive = self.scope["url_route"]["kwargs"].get("admin", False)
        if include_inactive and not self.scope["user"].is_staff:
            await self.close(code=4403)
            return
        stream = await _get_stream(slug, include_inactive=include_inactive)
        if not stream:
            await self.close(code=4404)
            return
        if not _redis_url():
            await self.close(code=1013)
            return
        self.slug = slug
        self.stream = stream
        self.start_id = _resolve_start_id(self.scope)
        await self.accept()
        self.redis = Redis.from_url(_redis_url(), decode_responses=False)
        self.stream_task = asyncio.create_task(self._stream_frames())

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "stream_task"):
            self.stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.stream_task
        if hasattr(self, "redis"):
            await self.redis.close()

    async def _stream_frames(self) -> None:
        if self.start_id == "$":
            cached = await database_sync_to_async(get_frame)(self.stream)
            if cached:
                await self.send(bytes_data=cached.frame_bytes)
        last_id = self.start_id
        stream_key = _stream_key(self.slug)
        while True:
            try:
                entries = await self.redis.xread(
                    {stream_key: last_id},
                    block=1000,
                    count=1,
                )
            except RedisError as exc:
                logger.warning("Redis stream read failed for %s: %s", self.slug, exc)
                await asyncio.sleep(0.2)
                continue
            if not entries:
                continue
            _, items = entries[0]
            for entry_id, fields in items:
                last_id = entry_id
                frame_bytes = fields.get(b"frame") or fields.get("frame")
                if not frame_bytes:
                    continue
                await self.send(bytes_data=frame_bytes)


class WebRTCSignalingConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        slug = self.scope["url_route"]["kwargs"]["slug"]
        include_inactive = self.scope["url_route"]["kwargs"].get("admin", False)
        if include_inactive and not self.scope["user"].is_staff:
            await self.close(code=4403)
            return
        stream = await _get_stream(slug, include_inactive=include_inactive)
        if not stream:
            await self.close(code=4404)
            return
        if not _redis_url():
            await self.close(code=1013)
            return
        self.slug = slug
        self.stream = stream
        self.start_id = _resolve_start_id(self.scope)
        self.pc = RTCPeerConnection()
        self.pc.addTrack(RedisVideoStreamTrack(slug, start_id=self.start_id))
        await self.accept()

    async def disconnect(self, code: int) -> None:
        if hasattr(self, "pc"):
            await self.pc.close()

    async def receive_json(self, content: dict[str, Any], **kwargs: Any) -> None:
        message_type = content.get("type")
        if message_type == "offer":
            offer = RTCSessionDescription(sdp=content.get("sdp", ""), type="offer")
            await self.pc.setRemoteDescription(offer)
            answer = await self.pc.createAnswer()
            await self.pc.setLocalDescription(answer)
            await self.send_json(
                {"type": "answer", "sdp": self.pc.localDescription.sdp}
            )
            return
        if message_type == "candidate":
            candidate = content.get("candidate")
            if candidate:
                if isinstance(candidate, dict):
                    await self.pc.addIceCandidate(RTCIceCandidate(**candidate))
                else:
                    await self.pc.addIceCandidate(candidate)
            return
        if message_type == "status":
            status = await database_sync_to_async(get_status)(self.stream)
            await self.send_json({"type": "status", "payload": status or {}})
            return
        await self.send_json({"type": "error", "message": "Unknown message type."})
