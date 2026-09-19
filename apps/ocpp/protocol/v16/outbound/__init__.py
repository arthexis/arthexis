"""Explicit OCPP 1.6 outbound request validation and composition."""

from apps.ocpp.protocol.v16.outbound.control import validate_control
from apps.ocpp.protocol.v16.outbound.operations import validate_operation
from apps.ocpp.protocol.v16.outbound.profiles import validate_profile
from apps.ocpp.protocol.v16.outbound.reservations import validate_reservation

VALIDATORS = {
    **validate_control,
    **validate_operation,
    **validate_profile,
    **validate_reservation,
}


def validate_outbound(action: str, payload: dict[str, object]) -> dict[str, object]:
    """Validate one supported OCPP 1.6 outbound payload without sending it."""
    validator = VALIDATORS.get(action)
    if validator is None:
        raise ValueError(f"Unsupported OCPP 1.6 outbound action: {action}")
    return validator(payload)
