"""OCPP 1.6 transfer, diagnostics, and firmware request validation."""

from collections.abc import Callable

from apps.ocpp.protocol.v16.outbound.common import require

Validator = Callable[[dict[str, object]], dict[str, object]]


validate_operation: dict[str, Validator] = {
    "DataTransfer": lambda payload: require(payload, "vendorId"),
    "GetDiagnostics": lambda payload: require(payload, "location"),
    "TriggerMessage": lambda payload: require(payload, "requestedMessage"),
    "UpdateFirmware": lambda payload: require(payload, "location", "retrieveDate"),
}
