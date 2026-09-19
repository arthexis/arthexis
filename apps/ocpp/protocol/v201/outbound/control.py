"""OCPP 2.0.1 explicit charger-control request validation."""

from apps.ocpp.protocol.v201.outbound.common import require


def _empty(payload: dict[str, object]) -> dict[str, object]:
    return payload


VALIDATORS = {
    "CancelReservation": lambda payload: require(payload, "reservationId"),
    "ChangeAvailability": lambda payload: require(payload, "operationalStatus"),
    "ClearChargingProfile": _empty,
    "DataTransfer": lambda payload: require(payload, "vendorId"),
    "GetCompositeSchedule": lambda payload: require(payload, "duration", "evseId"),
    "GetLocalListVersion": _empty,
    "RequestStartTransaction": lambda payload: require(
        payload, "idToken", "remoteStartId"
    ),
    "RequestStopTransaction": lambda payload: require(payload, "transactionId"),
    "Reset": lambda payload: require(payload, "type"),
    "ReserveNow": lambda payload: require(payload, "expiryDate", "id", "idToken"),
    "SendLocalList": lambda payload: require(payload, "version", "updateType"),
    "SetChargingProfile": lambda payload: require(payload, "chargingProfile", "evseId"),
    "TriggerMessage": lambda payload: require(payload, "requestedMessage"),
    "UnlockConnector": lambda payload: require(payload, "connectorId", "evseId"),
}
