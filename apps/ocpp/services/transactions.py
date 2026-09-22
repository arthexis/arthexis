"""Crash-safe transaction-family protocol services."""

from django.db import transaction

from apps.ocpp.domain.sessions import record_v201_transaction_event, start_transaction
from apps.ocpp.models import Charger, InboundProtocolRequest, OcppTransaction
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


@transaction.atomic
def process_v201_transaction_event(
    *,
    charger: Charger,
    payload: dict[str, object],
    replay_request: InboundProtocolRequest | None = None,
) -> dict[str, object]:
    """Persist one OCPP 2.0.1 transaction event with replay-safe authorization."""
    event_type = _required_text(payload, "eventType")
    transaction_id = _transaction_id(payload)
    existing = OcppTransaction.objects.filter(
        charger=charger,
        remote_id=transaction_id,
    ).exists()
    historical = payload.get("offline") is True or existing

    id_token = _optional_id_token(payload)
    if event_type == "Started" and not historical:
        id_token = _id_token(payload)
        authorization = authorize_id_tag(charger=charger, id_tag=id_token)
        if not authorization.accepted:
            response = {"idTokenInfo": {"status": "Invalid"}}
            if replay_request is not None:
                complete_with_result(replay_request, payload=response)
            return response

    evse = payload.get("evse")
    record_v201_transaction_event(
        charger=charger,
        event_type=event_type,
        transaction_id=transaction_id,
        id_token=id_token,
        evse_id=_optional_int(evse, "id"),
        connector_id=_optional_int(evse, "connectorId"),
        timestamp=payload.get("timestamp"),
    )
    response = {"idTokenInfo": {"status": "Accepted"}}
    if replay_request is not None:
        complete_with_result(replay_request, payload=response)
    return response


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


def _optional_int(payload: object, name: str) -> int | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(name)
    return value if isinstance(value, int) else None
