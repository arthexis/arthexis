from __future__ import annotations

import asyncio
from asyncio import subprocess as aio_subprocess
from array import array
from contextlib import suppress
import json
import logging
import shutil
from typing import Iterable

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .models import Node, NodeFeature


logger = logging.getLogger(__name__)


AUDIO_CAPTURE_SOCKET_PATH = "/ws/audio-capture/"


class AudioCaptureConsumer(AsyncWebsocketConsumer):
    """Stream live waveform data from the node microphone."""

    SAMPLE_RATE = 16_000
    CHUNK_SAMPLES = 2_048
    POINTS_PER_MESSAGE = 256

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.process: asyncio.subprocess.Process | None = None
        self.stream_task: asyncio.Task | None = None
        self._connection_open = False

    async def send_json(self, content: dict[str, object], close: bool = False):
        await self.send(text_data=json.dumps(content), close=close)

    async def connect(self):
        feature_enabled = await sync_to_async(self._is_feature_enabled)()
        if not feature_enabled:
            await self.close(code=4403)
            return

        has_device = await sync_to_async(Node._has_audio_capture_device)()
        if not has_device:
            await self.close(code=4404)
            return

        arecord_path = await sync_to_async(shutil.which)("arecord")
        if not arecord_path:
            await self.close(code=4405)
            return

        await self.accept()
        self._connection_open = True
        await self._send_status("Connecting to node microphone…")

        try:
            self.process = await asyncio.create_subprocess_exec(
                arecord_path,
                "-q",
                "-f",
                "S16_LE",
                "-r",
                str(self.SAMPLE_RATE),
                "-c",
                "1",
                "-t",
                "raw",
                "-",
                stdout=aio_subprocess.PIPE,
                stderr=aio_subprocess.PIPE,
            )
        except Exception:
            logger.exception("Audio capture recorder failed to start")
            await self._send_error("Unable to access the node microphone.")
            await self.close(code=1011)
            return

        self.stream_task = asyncio.create_task(self._stream_audio())

    async def disconnect(self, code):  # pragma: no cover - exercised indirectly
        self._connection_open = False
        if self.stream_task:
            self.stream_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.stream_task
            self.stream_task = None
        await self._stop_process()

    async def _stream_audio(self):
        process = self.process
        if not process or not process.stdout:
            await self._send_error("Audio capture stream unavailable.")
            await self.close(code=1011)
            return

        await self._send_status("Streaming from node microphone.")
        try:
            read_size = self.CHUNK_SAMPLES * 2
            while True:
                chunk = await process.stdout.read(read_size)
                if not chunk:
                    break
                points = self._chunk_to_points(chunk)
                if not points:
                    continue
                await self.send_json({"type": "waveform", "points": points})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Audio capture stream encountered an error")
            await self._send_error("Audio capture failed unexpectedly.")
        finally:
            await self._stop_process()
            if self._connection_open:
                await self._send_status("Audio capture ended.")
                await self.close()

    async def _stop_process(self):
        process = self.process
        self.process = None
        if not process:
            return

        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except (asyncio.TimeoutError, ProcessLookupError):
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(asyncio.TimeoutError, ProcessLookupError):
                    await asyncio.wait_for(process.wait(), timeout=1)
        else:
            with suppress(Exception):
                await process.wait()

    @staticmethod
    def _chunk_to_points(chunk: bytes) -> list[float]:
        sample_count = len(chunk) // 2
        if sample_count == 0:
            return []

        samples = array("h")
        samples.frombytes(chunk[: sample_count * 2])

        max_points = AudioCaptureConsumer.POINTS_PER_MESSAGE
        step = max(1, sample_count // max_points)
        limit = max_points * step
        values: Iterable[int] = samples[:limit:step]

        scale = 32768.0
        points = [max(-1.0, min(1.0, round(sample / scale, 4))) for sample in values]
        if len(points) < max_points and len(points) < sample_count:
            remainder = sample_count % step
            if remainder:
                points.append(max(-1.0, min(1.0, round(samples[-1] / scale, 4))))
        return points

    @staticmethod
    def _is_feature_enabled() -> bool:
        try:
            feature = NodeFeature.objects.get(slug="audio-capture")
        except NodeFeature.DoesNotExist:
            return False
        return feature.is_enabled

    async def _send_status(self, message: str):
        if not self._connection_open:
            return
        await self.send_json({"type": "status", "message": message})

    async def _send_error(self, message: str):
        if not self._connection_open:
            return
        await self.send_json({"type": "error", "message": message})


__all__ = ["AUDIO_CAPTURE_SOCKET_PATH", "AudioCaptureConsumer"]
