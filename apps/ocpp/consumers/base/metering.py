"""Meter value persistence and normalization helpers.

Supports OCPP 1.6 ``MeterValues`` and OCPP 2.x meter sampling included in
transaction events. DB side effects include writes to ``MeterValue`` and
transaction meter snapshots through delegated consumer methods.
"""

from typing import Protocol

from apps.ocpp.payload_types import HandlerPayload, HandlerResponse


class MeteringConsumer(Protocol):
    async def _handle_meter_values_legacy(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse: ...


class MeteringHandler:
    """Adapter for meter ingestion functions with DB write side effects."""

    def __init__(self, consumer: MeteringConsumer) -> None:
        self.consumer = consumer

    async def handle_meter_values(
        self, payload: HandlerPayload, msg_id: str, raw: str | None, text_data: str | None
    ) -> HandlerResponse:
        """Process ``MeterValues`` calls and persist normalized meter samples."""

        return await self.consumer._handle_meter_values_legacy(
            payload, msg_id, raw, text_data
        )
