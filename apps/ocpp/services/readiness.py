"""Read-only OCPP field-readiness diagnostics for trusted charger intake."""

from django.conf import settings

from apps.ocpp.models import OcppPolicy
from apps.ocpp.transport.listener import interface_addresses


def query_ocpp_readiness() -> dict[str, object]:
    """Describe effective OCPP intake policy and bounded transport settings."""

    interface = settings.OCPP_TRUSTED_CHARGER_INTERFACE
    addresses = sorted(interface_addresses(interface)) if interface else []
    policy = OcppPolicy.load()
    trusted_enabled = bool(interface)
    trusted_ready = trusted_enabled and bool(addresses)

    if not trusted_enabled:
        trusted_status = "disabled"
    elif not addresses:
        trusted_status = "unavailable"
    else:
        trusted_status = "ready"

    return {
        "trusted_interface": interface or None,
        "trusted_interface_addresses": addresses,
        "trusted_listener_status": trusted_status,
        "trusted_listener_admission": "open" if trusted_ready else None,
        "instance_charger_admission": policy.charger_admission_mode,
        "max_connections": settings.OCPP_MAX_CONNECTIONS,
        "websocket_connect_timeout_seconds": (
            settings.OCPP_WEBSOCKET_CONNECT_TIMEOUT_SECONDS
        ),
        "websocket_ping_interval_seconds": (
            settings.OCPP_WEBSOCKET_PING_INTERVAL_SECONDS
        ),
        "websocket_ping_timeout_seconds": (
            settings.OCPP_WEBSOCKET_PING_TIMEOUT_SECONDS
        ),
        "event_dispatch_batch_size": settings.EVENT_DISPATCH_BATCH_SIZE,
        "network_boundary_owner": "gway",
    }
