"""OCPP 1.6 charging-profile request validation."""

from collections.abc import Callable

from apps.ocpp.protocol.v16.outbound.common import optional, require

Validator = Callable[[dict[str, object]], dict[str, object]]


validate_profile: dict[str, Validator] = {
    "ClearChargingProfile": optional,
    "GetCompositeSchedule": lambda payload: require(payload, "connectorId", "duration"),
    "SetChargingProfile": lambda payload: require(
        payload, "connectorId", "csChargingProfiles"
    ),
}
