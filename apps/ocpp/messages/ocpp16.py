from __future__ import annotations

from .base import build_request_model, build_response_model
from .types import RegistrationStatus


REQUEST_MODELS = {
    "BootNotification": build_request_model(
        "BootNotificationRequest16",
        action="BootNotification",
        required_fields={"chargePointVendor": str, "chargePointModel": str},
    ),
    "DataTransfer": build_request_model("DataTransferRequest16", action="DataTransfer"),
    "Heartbeat": build_request_model("HeartbeatRequest16", action="Heartbeat"),
    "StatusNotification": build_request_model(
        "StatusNotificationRequest16", action="StatusNotification"
    ),
    "Authorize": build_request_model(
        "AuthorizeRequest16", action="Authorize", required_fields={"idTag": str}
    ),
    "MeterValues": build_request_model("MeterValuesRequest16", action="MeterValues"),
    "StartTransaction": build_request_model(
        "StartTransactionRequest16", action="StartTransaction"
    ),
    "StopTransaction": build_request_model(
        "StopTransactionRequest16", action="StopTransaction"
    ),
    "DiagnosticsStatusNotification": build_request_model(
        "DiagnosticsStatusNotificationRequest16",
        action="DiagnosticsStatusNotification",
    ),
    "FirmwareStatusNotification": build_request_model(
        "FirmwareStatusNotificationRequest16",
        action="FirmwareStatusNotification",
    ),
    "GetConfiguration": build_request_model(
        "GetConfigurationRequest16", action="GetConfiguration"
    ),
    "ReserveNow": build_request_model("ReserveNowRequest16", action="ReserveNow"),
    "RemoteStopTransaction": build_request_model(
        "RemoteStopTransactionRequest16", action="RemoteStopTransaction"
    ),
    "RemoteStartTransaction": build_request_model(
        "RemoteStartTransactionRequest16", action="RemoteStartTransaction"
    ),
    "GetDiagnostics": build_request_model(
        "GetDiagnosticsRequest16", action="GetDiagnostics"
    ),
    "ChangeAvailability": build_request_model(
        "ChangeAvailabilityRequest16", action="ChangeAvailability"
    ),
    "ChangeConfiguration": build_request_model(
        "ChangeConfigurationRequest16", action="ChangeConfiguration"
    ),
    "ClearCache": build_request_model("ClearCacheRequest16", action="ClearCache"),
    "CancelReservation": build_request_model(
        "CancelReservationRequest16", action="CancelReservation"
    ),
    "UnlockConnector": build_request_model(
        "UnlockConnectorRequest16", action="UnlockConnector"
    ),
    "Reset": build_request_model("ResetRequest16", action="Reset"),
    "TriggerMessage": build_request_model(
        "TriggerMessageRequest16", action="TriggerMessage"
    ),
    "SendLocalList": build_request_model(
        "SendLocalListRequest16", action="SendLocalList"
    ),
    "GetLocalListVersion": build_request_model(
        "GetLocalListVersionRequest16", action="GetLocalListVersion"
    ),
    "UpdateFirmware": build_request_model(
        "UpdateFirmwareRequest16", action="UpdateFirmware"
    ),
    "SetChargingProfile": build_request_model(
        "SetChargingProfileRequest16", action="SetChargingProfile"
    ),
}

RESPONSE_MODELS = {
    "BootNotification": build_response_model(
        "BootNotificationResponse16",
        action="BootNotification",
        required_fields={
            "status": (str, RegistrationStatus),
            "currentTime": str,
            "interval": int,
        },
    ),
    "DataTransfer": build_response_model("DataTransferResponse16", action="DataTransfer"),
    "Heartbeat": build_response_model("HeartbeatResponse16", action="Heartbeat"),
    "StatusNotification": build_response_model(
        "StatusNotificationResponse16", action="StatusNotification"
    ),
    "Authorize": build_response_model(
        "AuthorizeResponse16",
        action="Authorize",
        required_fields={
            "idTagInfo": dict,
        },
    ),
    "MeterValues": build_response_model("MeterValuesResponse16", action="MeterValues"),
    "StartTransaction": build_response_model(
        "StartTransactionResponse16", action="StartTransaction"
    ),
    "StopTransaction": build_response_model(
        "StopTransactionResponse16", action="StopTransaction"
    ),
    "DiagnosticsStatusNotification": build_response_model(
        "DiagnosticsStatusNotificationResponse16",
        action="DiagnosticsStatusNotification",
    ),
    "FirmwareStatusNotification": build_response_model(
        "FirmwareStatusNotificationResponse16",
        action="FirmwareStatusNotification",
    ),
    "GetConfiguration": build_response_model(
        "GetConfigurationResponse16", action="GetConfiguration"
    ),
    "ReserveNow": build_response_model("ReserveNowResponse16", action="ReserveNow"),
    "RemoteStopTransaction": build_response_model(
        "RemoteStopTransactionResponse16", action="RemoteStopTransaction"
    ),
    "RemoteStartTransaction": build_response_model(
        "RemoteStartTransactionResponse16", action="RemoteStartTransaction"
    ),
    "GetDiagnostics": build_response_model(
        "GetDiagnosticsResponse16", action="GetDiagnostics"
    ),
    "ChangeAvailability": build_response_model(
        "ChangeAvailabilityResponse16", action="ChangeAvailability"
    ),
    "ChangeConfiguration": build_response_model(
        "ChangeConfigurationResponse16", action="ChangeConfiguration"
    ),
    "ClearCache": build_response_model("ClearCacheResponse16", action="ClearCache"),
    "CancelReservation": build_response_model(
        "CancelReservationResponse16", action="CancelReservation"
    ),
    "UnlockConnector": build_response_model(
        "UnlockConnectorResponse16", action="UnlockConnector"
    ),
    "Reset": build_response_model("ResetResponse16", action="Reset"),
    "TriggerMessage": build_response_model(
        "TriggerMessageResponse16", action="TriggerMessage"
    ),
    "SendLocalList": build_response_model(
        "SendLocalListResponse16", action="SendLocalList"
    ),
    "GetLocalListVersion": build_response_model(
        "GetLocalListVersionResponse16", action="GetLocalListVersion"
    ),
    "UpdateFirmware": build_response_model(
        "UpdateFirmwareResponse16", action="UpdateFirmware"
    ),
    "SetChargingProfile": build_response_model(
        "SetChargingProfileResponse16", action="SetChargingProfile"
    ),
}
