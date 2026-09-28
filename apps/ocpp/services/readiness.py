"""Read-only OCPP readiness and permissiveness evaluation."""

from __future__ import annotations

from django.conf import settings

from apps.ocpp.models import Charger, OcppPolicy
from apps.ocpp.transport.listener import interface_addresses

PERMISSIVE = "permissive"
STRICT = "strict"
PARTIAL = "partial"
DIMENSIONS = ("admission", "protocol", "cards")
MODES = (PERMISSIVE, STRICT)


def _mode(value: str) -> str:
    """Translate persisted OPEN/RESTRICTED vocabulary into readiness vocabulary."""
    if value == OcppPolicy.AdmissionMode.OPEN:
        return PERMISSIVE
    if value == OcppPolicy.AdmissionMode.RESTRICTED:
        return STRICT
    raise ValueError(f"Unsupported OCPP policy mode: {value}")


def query_ocpp_readiness() -> dict[str, object]:
    """Describe instance OCPP readiness and its default policy posture."""

    interface = settings.OCPP_TRUSTED_CHARGER_INTERFACE
    addresses = sorted(interface_addresses(interface)) if interface else []
    policy = OcppPolicy.load()

    if not interface:
        listener_status = "disabled"
    elif not addresses:
        listener_status = "unavailable"
    else:
        listener_status = "ready"

    dimensions = {
        "admission": _mode(policy.charger_admission_mode),
        "protocol": _mode(policy.protocol_mode),
        "cards": _mode(policy.card_mode),
    }
    unique_modes = set(dimensions.values())
    posture = next(iter(unique_modes)) if len(unique_modes) == 1 else PARTIAL

    return {
        "ready": listener_status == "ready",
        "posture": posture,
        **dimensions,
        "trusted_interface": interface or None,
        "trusted_interface_addresses": addresses,
        "trusted_listener_status": listener_status,
        "max_connections": settings.OCPP_MAX_CONNECTIONS,
        "websocket_connect_timeout_seconds": settings.OCPP_WEBSOCKET_CONNECT_TIMEOUT_SECONDS,
        "websocket_ping_interval_seconds": settings.OCPP_WEBSOCKET_PING_INTERVAL_SECONDS,
        "websocket_ping_timeout_seconds": settings.OCPP_WEBSOCKET_PING_TIMEOUT_SECONDS,
        "event_dispatch_batch_size": settings.EVENT_DISPATCH_BATCH_SIZE,
        "network_boundary_owner": "gway",
    }


def query_charger_ocpp_readiness(identity: str) -> dict[str, object]:
    """Describe effective OCPP readiness for one configured charger."""

    try:
        charger = Charger.objects.get(identity=identity)
    except Charger.DoesNotExist as error:
        raise ValueError(f"Unknown charger: {identity}") from error

    instance = query_ocpp_readiness()
    dimensions = {
        "protocol": _mode(charger.protocol_mode),
        "cards": _mode(charger.authorization_mode),
    }
    unique_modes = set(dimensions.values())
    posture = next(iter(unique_modes)) if len(unique_modes) == 1 else PARTIAL

    return {
        "charger": charger.identity,
        "ready": bool(instance["ready"] and charger.active),
        "enabled": charger.active,
        "posture": posture,
        **dimensions,
    }


def evaluate_ocpp_readiness(
    dimension: str | None = None,
    mode: str | None = None,
    *,
    charger: str | None = None,
) -> bool | str | dict[str, object]:
    """Return the compact result for the ready OCPP operation."""

    valid_dimensions = ("protocol", "cards") if charger else DIMENSIONS
    if dimension is not None and dimension not in valid_dimensions:
        raise ValueError(
            "OCPP readiness dimension must be one of: "
            + ", ".join(valid_dimensions)
        )
    if mode is not None and mode not in MODES:
        raise ValueError(
            "OCPP readiness mode must be one of: " + ", ".join(MODES)
        )
    if mode is not None and dimension is None:
        raise ValueError("OCPP readiness mode requires a dimension.")

    payload = (
        query_charger_ocpp_readiness(charger)
        if charger
        else query_ocpp_readiness()
    )
    if not payload["ready"]:
        return False

    if charger and dimension is None and mode is None:
        return payload

    result = payload[dimension] if dimension else payload["posture"]
    if mode is None:
        return result
    return result == mode
