"""OCPP 1.6 reservation and authorization-list request validation."""

from collections.abc import Callable

from apps.ocpp.protocol.v16.outbound.common import optional, require

Validator = Callable[[dict[str, object]], dict[str, object]]


validate_reservation: dict[str, Validator] = {
    "CancelReservation": lambda payload: require(payload, "reservationId"),
    "GetLocalListVersion": optional,
    "ReserveNow": lambda payload: require(
        payload,
        "connectorId",
        "expiryDate",
        "idTag",
        "reservationId",
    ),
    "SendLocalList": lambda payload: require(payload, "listVersion", "updateType"),
}
