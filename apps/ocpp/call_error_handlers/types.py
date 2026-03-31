"""Type contracts for OCPP call error handlers."""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Mapping, Protocol, TypeAlias, TypedDict

JsonPrimitive: TypeAlias = bool | float | int | str | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

CallMessagePayload: TypeAlias = dict[str, JsonValue]
CallResultPayload: TypeAlias = dict[str, JsonValue]
CallMetadata: TypeAlias = Mapping[str, object]
CallErrorDetails: TypeAlias = dict[str, JsonValue]


class CallErrorPayload(TypedDict):
    """Normalized payload for OCPP CALLERROR messages."""

    message_id: str
    error_code: str | None
    description: str | None
    details: CallErrorDetails | None


class CallErrorContext(Protocol):
    """Protocol required by call error handlers."""

    charger_id: str | None
    store_key: str

    async def _update_local_authorization_state(self, version: int | None) -> None:
        ...

    async def _update_change_availability_state(
        self,
        connector_value: int | None,
        requested_type: str | None,
        status: str,
        requested_at: datetime | str | None,
        *,
        details: str = "",
    ) -> None:
        ...


CallErrorHandler = Callable[
    [CallErrorContext, str, CallMetadata, str | None, str | None, CallErrorDetails | None, str],
    Awaitable[bool],
]


__all__ = [
    "CallErrorContext",
    "CallErrorDetails",
    "CallErrorHandler",
    "CallErrorPayload",
    "CallMessagePayload",
    "CallMetadata",
    "CallResultPayload",
    "JsonPrimitive",
    "JsonValue",
]
