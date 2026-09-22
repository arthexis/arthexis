"""Crash-safe transaction-family protocol services."""

from django.db import transaction

from apps.ocpp.domain.sessions import start_transaction
from apps.ocpp.models import Charger, InboundProtocolRequest
from apps.ocpp.services.authorization import authorize_id_tag
from apps.ocpp.services.replay import complete_with_result


@transaction.atomic
def process_v16_start_transaction(
    *,
    charger: Charger,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Authorize and persist one OCPP 1.6 start before its replay ACK is durable."""
    id_tag = _required_text(payload, "idTag")
    connector_id = _required_int(payload, "connectorId")

    authorization = authorize_id_tag(charger=charger, id_tag=id_tag)
    if not authorization.accepted:
        response = {"idTagInfo": {"status": "Invalid"}}
    else:
        selected = start_transaction(
            charger=charger,
            connector_id=connector_id,
            id_tag=id_tag,
            account=authorization.account,
            meter_start=payload.get("meterStart"),
            timestamp=payload.get("timestamp"),
        )
        response = {
            "idTagInfo": {"status": "Accepted"},
            "transactionId": selected.pk,
        }

    if replay_request is not None:
        complete_with_result(replay_request, payload=response)
    return response


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
