from __future__ import annotations

from typing import Awaitable, Callable, Protocol


class CallResultContext(Protocol):
    charger_id: str | None
    store_key: str
    charger: object | None
    aggregate_charger: object | None

    async def _update_local_authorization_state(self, version: int | None) -> None:
        ...

    async def _apply_local_authorization_entries(self, entries) -> int:
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

    def _apply_change_configuration_snapshot(
        self, key: str, value: str | None, connector_hint: int | str | None
    ) -> object:
        ...

    def _persist_configuration_result(self, payload: dict, connector_id) -> object | None:
        ...


class CallErrorContext(Protocol):
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


CallResultHandler = Callable[
    [CallResultContext, str, dict, dict, str],
    Awaitable[bool],
]

CallErrorHandler = Callable[
    [CallErrorContext, str, dict, str | None, str | None, dict | None, str],
    Awaitable[bool],
]
