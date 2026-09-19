"""OCPP 1.6 authorization, session, status, and metering handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.ocpp.domain.sessions import (
    record_meter_values,
    start_transaction,
    stop_transaction,
)
from apps.ocpp.models import Charger, Connector
from apps.ocpp.services.authorization import authorize_id_tag

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
        return {
            "currentTime": timezone.now().isoformat(),
            "interval": 300,
            "status": "Accepted",
        }

    async def heartbeat(self, payload: dict[str, object]) -> dict[str, object]:
        await self._record_connection()
        return {"currentTime": timezone.now().isoformat()}

    async def meter_values(self, payload: dict[str, object]) -> dict[str, object]:
        await sync_to_async(record_meter_values)(
            transaction_id=int(payload["transactionId"]),
            charger=self.charger,
            meter_values=payload["meterValue"],
        )
        return {}

    async def start_transaction(self, payload: dict[str, object]) -> dict[str, object]:
        id_tag = _required_text(payload, "idTag")
        authorization = await sync_to_async(authorize_id_tag)(
            charger=self.charger,
            id_tag=id_tag,
        )
        if not authorization.accepted:
            return {"idTagInfo": {"status": "Invalid"}}
        transaction = await sync_to_async(start_transaction)(
            charger=self.charger,
            connector_id=int(payload["connectorId"]),
            id_tag=id_tag,
            account=authorization.account,
            meter_start=payload.get("meterStart"),
            timestamp=payload.get("timestamp"),
        )
        return {"idTagInfo": {"status": "Accepted"}, "transactionId": transaction.pk}

    async def status_notification(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        await self._set_connector_status(
            int(payload["connectorId"]), _required_text(payload, "status")
        )
        return {}

    async def stop_transaction(self, payload: dict[str, object]) -> dict[str, object]:
        await sync_to_async(stop_transaction)(
            transaction_id=int(payload["transactionId"]),
            charger=self.charger,
            meter_stop=payload.get("meterStop"),
            timestamp=payload.get("timestamp"),
        )
        return {"idTagInfo": {"status": "Accepted"}}

    @sync_to_async
    def _record_connection(self) -> None:
        Charger.objects.filter(pk=self.charger.pk).update(connected_at=timezone.now())

    @sync_to_async
    def _set_connector_status(self, connector_id: int, status: str) -> None:
        Connector.objects.update_or_create(
            charger=self.charger,
            number=connector_id,
            defaults={"status": status},
        )


def _required_text(payload: dict[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value
