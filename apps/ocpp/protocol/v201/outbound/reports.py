"""OCPP 2.0.1 customer, display, report, and firmware request validation."""

from apps.ocpp.protocol.v201.outbound.common import require

VALIDATORS = {
    "ClearDisplayMessage": lambda payload: require(payload, "id"),
    "CustomerInformation": lambda payload: require(payload, "reportBase", "requestId"),
    "GetBaseReport": lambda payload: require(payload, "reportBase", "requestId"),
    "GetDisplayMessages": lambda payload: require(payload, "requestId"),
    "GetLog": lambda payload: require(
        payload, "logType", "remoteLocation", "requestId"
    ),
    "GetReport": lambda payload: require(payload, "componentVariable", "requestId"),
    "PublishFirmware": lambda payload: require(payload, "location", "requestId"),
    "SetDisplayMessage": lambda payload: require(payload, "message"),
    "UpdateFirmware": lambda payload: require(payload, "firmware", "requestId"),
}
