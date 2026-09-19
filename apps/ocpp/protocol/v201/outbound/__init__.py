"""Explicit OCPP 2.0.1 outbound request validation and composition."""

from apps.ocpp.protocol.v201.matrix import OUTBOUND_ACTIONS as ACTIONS
from apps.ocpp.protocol.v201.outbound.certificates import VALIDATORS as certificates
from apps.ocpp.protocol.v201.outbound.configuration import VALIDATORS as configuration
from apps.ocpp.protocol.v201.outbound.control import VALIDATORS as control
from apps.ocpp.protocol.v201.outbound.reports import VALIDATORS as reports

VALIDATORS = {**certificates, **configuration, **control, **reports}


def validate_outbound(action: str, payload: dict[str, object]) -> dict[str, object]:
    """Validate one supported OCPP 2.0.1 outbound payload without sending it."""
    validator = VALIDATORS.get(action)
    if validator is None:
        raise ValueError(f"Unsupported OCPP 2.0.1 outbound action: {action}")
    return validator(payload)
