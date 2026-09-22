"""OCPP 2.0.1 notification, status, limit, and reservation handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async

from apps.ocpp.models import Charger
from apps.ocpp.services.intake import (
    process_data_transfer,
    process_generic_notification,
    process_operational_status,
)

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class NotificationActions:
    """Persist retained OCPP 2.0.1 notifications without initiating work."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "ClearedChargingLimit": self._generic("ClearedChargingLimit"),
            "CostUpdated": self._generic("CostUpdated"),
            "DataTransfer": self.data_transfer,
            "FirmwareStatusNotification": self.firmware_status,
            "LogStatusNotification": self.log_status,
            "NotifyChargingLimit": self._generic("NotifyChargingLimit"),
            "NotifyCustomerInformation": self._generic("NotifyCustomerInformation"),
            "NotifyDisplayMessages": self._generic("NotifyDisplayMessages"),
            "NotifyEVChargingNeeds": self._generic("NotifyEVChargingNeeds"),
            "NotifyEVChargingSchedule": self._generic("NotifyEVChargingSchedule"),
            "NotifyEvent": self._generic("NotifyEvent"),
            "PublishFirmwareStatusNotification": self.publish_firmware_status,
            "ReservationStatusUpdate": self._generic("ReservationStatusUpdate"),
            "SecurityEventNotification": self._generic("SecurityEventNotification"),
        }

    async def data_transfer(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_data_transfer)(
            charger=self.charger,
            payload=payload,
        )

    async def firmware_status(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_operational_status)(
            charger=self.charger,
            action="FirmwareStatusNotification",
            payload=payload,
        )

    async def publish_firmware_status(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        return await sync_to_async(process_operational_status)(
            charger=self.charger,
            action="PublishFirmwareStatusNotification",
            payload=payload,
        )

    async def log_status(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_operational_status)(
            charger=self.charger,
            action="LogStatusNotification",
            payload=payload,
        )

    def _generic(self, action: str) -> Handler:
        async def acknowledge(payload: dict[str, object]) -> dict[str, object]:
            return await sync_to_async(process_generic_notification)(
                charger=self.charger,
                action=action,
                payload=payload,
            )

        return acknowledge


def _required_text(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value
