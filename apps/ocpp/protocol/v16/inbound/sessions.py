"""OCPP 1.6 authorization, session, status, and metering handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.ocpp.domain.sessions import reconcile_connector_status
from apps.ocpp.models import Charger
from apps.ocpp.services.authorization import authorize_id_tag
from apps.ocpp.services.presence import configure_heartbeat
from apps.ocpp.services.transactions import (
    process_v16_meter_values,
    process_v16_start_transaction,
    process_v16_stop_transaction,
)

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class SessionActions:
    """Handle retained OCPP 1.6 charging-session action families."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "Authorize": self.authorize,
            "BootNotification": self.boot_notification,
            "Heartbeat": self.heartbeat,
            "MeterValues": self.meter_values,
            "StartTransaction": self.start_transaction,
            "StatusNotification": self.status_notification,
            "StopTransaction": self.stop_transaction,
        }

    async def authorize(self, payload: dict[str, object]) -> dict[str, object]:
        result = await sync_to_async(authorize_id_tag)(
            charger=self.charger,
            id_tag=_required_text(payload, "idTag"),
        )
        return {"idTagInfo": {"status": "Accepted" if result.accepted else "Invalid"}}

    async def boot_notification(self, payload: dict[str, object]) -> dict[str, object]:
        _required_text(payload, "chargePointVendor")
        _required_text(payload, "chargePointModel")
        await self._record_connection()
        interval = 300
        await sync_to_async(configure_heartbeat)(
            charger=self.charger,
            interval_seconds=interval,
        )
        return {
            "currentTime": timezone.now().isoformat(),
            "interval": interval,
            "status": "Accepted",
        }

    async def heartbeat(self, payload: dict[str, object]) -> dict[str, object]:
        await self._record_connection()
        return {"currentTime": timezone.now().isoformat()}

    async def meter_values(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_v16_meter_values)(
            charger=self.charger,
            payload=payload,
        )

    async def start_transaction(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_v16_start_transaction)(
            charger=self.charger,
            payload=payload,
        )

    async def status_notification(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        await sync_to_async(reconcile_connector_status)(
            charger=self.charger,
            connector_number=int(payload["connectorId"]),
            status=_required_text(payload, "status"),
            observed_at=payload.get("timestamp"),
        )
        return {}

    async def stop_transaction(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_v16_stop_transaction)(
            charger=self.charger,
            payload=payload,
        )

    @sync_to_async
    def _record_connection(self) -> None:
        Charger.objects.filter(pk=self.charger.pk).update(connected_at=timezone.now())


def _required_text(payload: dict[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value
