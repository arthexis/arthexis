"""Bounded in-memory correlation for one live OCPP connection."""

import asyncio
from uuid import uuid4

from apps.ocpp.protocol.errors import (
    ConnectionClosed,
    OutboundCallError,
    OutboundCallTimeout,
)
from apps.ocpp.protocol.frames import CallError, CallResult


class PendingCalls:
    """Track outbound calls until result, error, timeout, or disconnect."""

    def __init__(self) -> None:
        self._calls: dict[str, asyncio.Future[dict[str, object]]] = {}

    def open(
        self, unique_id: str | None = None
    ) -> tuple[str, asyncio.Future[dict[str, object]]]:
        unique_id = unique_id or str(uuid4())
        if unique_id in self._calls:
            raise ValueError(f"OCPP call ID is already pending: {unique_id}")
        future: asyncio.Future[dict[str, object]] = (
            asyncio.get_running_loop().create_future()
        )
        self._calls[unique_id] = future
        return unique_id, future

    async def wait(
        self, unique_id: str, future: asyncio.Future[dict[str, object]], timeout: float
    ) -> dict[str, object]:
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as error:
            raise OutboundCallTimeout(f"OCPP call {unique_id} timed out.") from error
        finally:
            self._calls.pop(unique_id, None)

    def resolve(self, frame: CallResult | CallError) -> bool:
        future = self._calls.get(frame.unique_id)
        if future is None or future.done():
            return False
        if isinstance(frame, CallResult):
            future.set_result(frame.payload)
        else:
            future.set_exception(OutboundCallError(frame.code, frame.description))
        return True

    def close(self) -> None:
        for future in self._calls.values():
            if not future.done():
                future.set_exception(ConnectionClosed("OCPP connection closed."))
        self._calls.clear()
