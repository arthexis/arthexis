"""OCPP 2.0.1 authorization, session, status, and metering handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.ocpp.domain.sessions import (
    record_v201_meter_values,
    record_v201_transaction_event,
)
from apps.ocpp.models import Charger, Connector
from apps.ocpp.services.authorization import authorize_id_tag

Handler = Callable[[dict[str, object]], Awaitable[dict[str, object]]]


class SessionActions:
    """Handle retained OCPP 2.0.1 charging-session actions."""

    def __init__(self, charger: Charger) -> None:
        self.charger = charger
        self.handlers: dict[str, Handler] = {
            "Authorize": self.authorize,
            "BootNotification": self.boot_notification,
            "Heartbeat": self.heartbeat,
            "MeterValues": self.meter_values,
            "StatusNotification": self.status_notification,
            "TransactionEvent": self.transaction_event,
        }

    async def authorize(self, payload: dict[str, object]) -> dict[str, object]:
        result = await sync_to_async(authorize_id_tag)(
            charger=self.charger, id_tag=_id_token(payload)
        )
        return {"idTokenInfo": {"status": "Accepted" if result.accepted else "Invalid"}}

    async def boot_notification(self, payload: dict[str, object]) -> dict[str, object]:
        station = payload.get("chargingStation")
        if not isinstance(station, dict):
            raise ValueError("chargingStation is required")
        _required_text(station, "vendorName")
        _required_text(station, "model")
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
        transaction = _transaction_id(payload)
        await sync_to_async(record_v201_meter_values)(
            charger=self.charger,
            transaction_id=transaction,
            meter_values=payload["meterValue"],
        )
        return {}

    async def status_notification(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        evse_id = _required_int(payload, "evseId")
        connector_id = _required_int(payload, "connectorId")
        await self._set_connector_status(
            evse_id, connector_id, _required_text(payload, "connectorStatus")
        )
        return {}

    async def transaction_event(self, payload: dict[str, object]) -> dict[str, object]:
        event_type = _required_text(payload, "eventType")
        id_token = _optional_id_token(payload)
        if event_type == "Started":
            id_token = _id_token(payload)
            authorization = await sync_to_async(authorize_id_tag)(
                charger=self.charger,
                id_tag=id_token,
            )
            if not authorization.accepted:
                return {"idTokenInfo": {"status": "Invalid"}}
        evse = payload.get("evse")
        evse_id = _optional_int(evse, "id")
        connector_id = _optional_int(evse, "connectorId")
        await sync_to_async(record_v201_transaction_event)(
            charger=self.charger,
            event_type=event_type,
            transaction_id=_transaction_id(payload),
            id_token=id_token,
            evse_id=evse_id,
            connector_id=connector_id,
            timestamp=payload.get("timestamp"),
        )
        return {"idTokenInfo": {"status": "Accepted"}}

    @sync_to_async
    def _record_connection(self) -> None:
        Charger.objects.filter(pk=self.charger.pk).update(connected_at=timezone.now())

    @sync_to_async
    def _set_connector_status(
        self, evse_id: int, connector_id: int, status: str
    ) -> None:
        Connector.objects.update_or_create(
            charger=self.charger,
            number=(evse_id * 1000) + connector_id,
            defaults={"status": status},
        )


def _id_token(payload: dict[str, object]) -> str:
    token = payload.get("idToken")
    if not isinstance(token, dict):
        raise ValueError("idToken is required")
    return _required_text(token, "idToken")


def _optional_id_token(payload: dict[str, object]) -> str:
    token = payload.get("idToken")
    return _required_text(token, "idToken") if isinstance(token, dict) else ""


def _transaction_id(payload: dict[str, object]) -> str:
    info = payload.get("transactionInfo")
    if not isinstance(info, dict):
        raise ValueError("transactionInfo is required")
    return _required_text(info, "transactionId")


def _required_text(payload: dict[str, object], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} is required")
    return value


def _required_int(payload: dict[str, object], name: str) -> int:
    value = payload.get(name)
    if not isinstance(value, int):
        raise ValueError(f"{name} is required")
    return value


def _optional_int(payload: object, name: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(name)
    return value if isinstance(value, int) else None
