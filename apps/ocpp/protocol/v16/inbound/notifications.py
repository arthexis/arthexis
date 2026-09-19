"""OCPP 1.6 transfer and operational-status acknowledgement handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.domain.notifications import (
    record_notification,
    record_operational_status,
)
from apps.ocpp.models import Charger, OperationalStatusRecord

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class NotificationActions:
    """Persist inbound OCPP 1.6 transfer and status messages without scheduling work."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "DataTransfer": self.data_transfer,
            "DiagnosticsStatusNotification": self.diagnostics_status,
            "FirmwareStatusNotification": self.firmware_status,
        }

    async def data_transfer(self, payload: dict[str, object]) -> dict[str, object]:
        vendor_id = payload.get("vendorId")
        if not isinstance(vendor_id, str) or not vendor_id:
            raise ValueError("vendorId is required")
        await sync_to_async(record_notification)(
            charger=self.charger,
            action="DataTransfer",
            payload=payload,
        )
        return {"status": "Accepted"}

    async def diagnostics_status(self, payload: dict[str, object]) -> dict[str, object]:
        await self._record_status(OperationalStatusRecord.Kind.DIAGNOSTICS, payload)
        return {}

    async def firmware_status(self, payload: dict[str, object]) -> dict[str, object]:
        await self._record_status(OperationalStatusRecord.Kind.FIRMWARE, payload)
        return {}

    async def _record_status(self, kind: str, payload: dict[str, object]) -> None:
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError("status is required")
        await sync_to_async(record_operational_status)(
            charger=self.charger,
            kind=kind,
            status=status,
            payload=payload,
        )
