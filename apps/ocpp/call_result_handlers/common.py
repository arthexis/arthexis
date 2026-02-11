"""Shared context and helper utilities for OCPP call result handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


class CallResultContext(Protocol):
    """Protocol implemented by the websocket consumer handling OCPP calls."""

    charger_id: str | None
    store_key: str
    charger: object | None
    aggregate_charger: object | None

    async def _update_local_authorization_state(self, version: int | None) -> None: ...

    async def _apply_local_authorization_entries(self, entries) -> int: ...

    async def _update_change_availability_state(
        self,
        connector_value: int | None,
        requested_type: str | None,
        status: str,
        requested_at,
        *,
        details: str = "",
    ) -> None: ...

    def _apply_change_configuration_snapshot(self, key: str, value: str | None, connector_hint): ...

    def _persist_configuration_result(self, payload: dict, connector_id): ...


@dataclass(slots=True)
class HandlerContext:
    """Normalized context passed to every domain handler.

    Attributes:
        consumer: Live consumer object owning persistence helpers.
        message_id: OCPP call message id being completed.
        metadata: Stored call metadata.
        payload: Decoded call result payload.
        log_key: Log bucket key used by in-memory store.
    """

    consumer: CallResultContext
    message_id: str
    metadata: dict[str, Any]
    payload: dict[str, Any]
    log_key: str


ContextHandler = Callable[[HandlerContext], Awaitable[bool]]
LegacyHandler = Callable[[CallResultContext, str, dict, dict, str], Awaitable[bool]]


def build_context(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload: dict,
    log_key: str,
) -> HandlerContext:
    """Construct a normalized handler context from legacy call parameters."""

    return HandlerContext(
        consumer=consumer,
        message_id=message_id,
        metadata=metadata,
        payload=payload,
        log_key=log_key,
    )


def legacy_adapter(handler: ContextHandler) -> LegacyHandler:
    """Adapt a context-based handler to the legacy call signature."""

    async def _wrapped(consumer, message_id, metadata, payload_data, log_key):
        return await handler(build_context(consumer, message_id, metadata, payload_data, log_key))

    _wrapped.__name__ = f"legacy_{getattr(handler, '__name__', 'handler')}"
    return _wrapped
