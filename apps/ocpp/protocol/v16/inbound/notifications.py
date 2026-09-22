"""OCPP 1.6 transfer and operational-status acknowledgement handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.models import Charger
from apps.ocpp.services.intake import process_data_transfer, process_operational_status

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
        return await sync_to_async(process_data_transfer)(
            charger=self.charger,
            payload=payload,
        )

    async def diagnostics_status(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_operational_status)(
            charger=self.charger,
            action="DiagnosticsStatusNotification",
            payload=payload,
        )

    async def firmware_status(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_operational_status)(
            charger=self.charger,
            action="FirmwareStatusNotification",
            payload=payload,
        )
