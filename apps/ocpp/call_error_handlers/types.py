"""Type contracts for OCPP call error handlers."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol


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
        requested_at,
        *,
        details: str = "",
    ) -> None:
        ...


CallErrorHandler = Callable[
    [CallErrorContext, str, dict, str | None, str | None, dict | None, str],
    Awaitable[bool],
]
