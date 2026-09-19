"""OCPP 1.6 availability, configuration, and connector-control requests."""

from collections.abc import Callable

from apps.ocpp.protocol.v16.outbound.common import optional, require

Validator = Callable[[dict[str, object]], dict[str, object]]


validate_control: dict[str, Validator] = {
    "ChangeAvailability": lambda payload: require(payload, "connectorId", "type"),
    "ChangeConfiguration": lambda payload: require(payload, "key", "value"),
    "GetConfiguration": optional,
    "RemoteStartTransaction": lambda payload: require(payload, "idTag"),
    "RemoteStopTransaction": lambda payload: require(payload, "transactionId"),
    "Reset": lambda payload: require(payload, "type"),
    "UnlockConnector": lambda payload: require(payload, "connectorId"),
}
