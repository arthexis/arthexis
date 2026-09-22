"""OCPP 2.0.1 authorization, session, status, and metering handlers."""

from collections.abc import Awaitable, Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone

from apps.ocpp.domain.sessions import reconcile_connector_status
from apps.ocpp.models import Charger
from apps.ocpp.services.authorization import authorize_id_tag
from apps.ocpp.services.metering import process_v201_standalone_meter_values
from apps.ocpp.services.presence import configure_heartbeat
from apps.ocpp.services.transactions import (
    process_v201_meter_values,
    process_v201_transaction_event,
)

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
        interval = settings.OCPP_HEARTBEAT_INTERVAL_SECONDS
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
        meter_values = payload["meterValue"]
        transaction_info = payload.get("transactionInfo")
        if isinstance(transaction_info, dict):
            return await sync_to_async(process_v201_meter_values)(
                charger=self.charger,
                payload=payload,
            )
        return await sync_to_async(process_v201_standalone_meter_values)(
            charger=self.charger,
            payload=payload,
        )

    async def status_notification(
        self, payload: dict[str, object]
    ) -> dict[str, object]:
        evse_id = _required_int(payload, "evseId")
        connector_id = _required_int(payload, "connectorId")
        await sync_to_async(reconcile_connector_status)(
            charger=self.charger,
            connector_number=(evse_id * 1000) + connector_id,
            status=_required_text(payload, "connectorStatus"),
            observed_at=payload.get("timestamp"),
        )
        return {}

    async def transaction_event(self, payload: dict[str, object]) -> dict[str, object]:
        return await sync_to_async(process_v201_transaction_event)(
            charger=self.charger,
            payload=payload,
        )

    @sync_to_async
    def _record_connection(self) -> None:
        Charger.objects.filter(pk=self.charger.pk).update(connected_at=timezone.now())


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
