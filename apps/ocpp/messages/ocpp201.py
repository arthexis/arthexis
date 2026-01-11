from __future__ import annotations

from .base import build_request_model, build_response_model
from .types import RegistrationStatus


REQUEST_MODELS = {
    "BootNotification": build_request_model(
        "BootNotificationRequest201",
        action="BootNotification",
        required_fields={"chargingStation": dict},
    ),
    "DataTransfer": build_request_model("DataTransferRequest201", action="DataTransfer"),
    "Heartbeat": build_request_model("HeartbeatRequest201", action="Heartbeat"),
    "StatusNotification": build_request_model(
        "StatusNotificationRequest201", action="StatusNotification"
    ),
    "Authorize": build_request_model(
        "AuthorizeRequest201",
        action="Authorize",
        required_fields={"idToken": dict},
    ),
    "MeterValues": build_request_model("MeterValuesRequest201", action="MeterValues"),
    "ClearedChargingLimit": build_request_model(
        "ClearedChargingLimitRequest201", action="ClearedChargingLimit"
    ),
    "NotifyReport": build_request_model("NotifyReportRequest201", action="NotifyReport"),
    "NotifyChargingLimit": build_request_model(
        "NotifyChargingLimitRequest201", action="NotifyChargingLimit"
    ),
    "NotifyCustomerInformation": build_request_model(
        "NotifyCustomerInformationRequest201", action="NotifyCustomerInformation"
    ),
    "NotifyDisplayMessages": build_request_model(
        "NotifyDisplayMessagesRequest201", action="NotifyDisplayMessages"
    ),
    "NotifyEVChargingNeeds": build_request_model(
        "NotifyEVChargingNeedsRequest201", action="NotifyEVChargingNeeds"
    ),
    "NotifyEVChargingSchedule": build_request_model(
        "NotifyEVChargingScheduleRequest201", action="NotifyEVChargingSchedule"
    ),
    "NotifyEvent": build_request_model("NotifyEventRequest201", action="NotifyEvent"),
    "NotifyMonitoringReport": build_request_model(
        "NotifyMonitoringReportRequest201", action="NotifyMonitoringReport"
    ),
    "PublishFirmwareStatusNotification": build_request_model(
        "PublishFirmwareStatusNotificationRequest201",
        action="PublishFirmwareStatusNotification",
    ),
    "ReportChargingProfiles": build_request_model(
        "ReportChargingProfilesRequest201", action="ReportChargingProfiles"
    ),
    "SecurityEventNotification": build_request_model(
        "SecurityEventNotificationRequest201", action="SecurityEventNotification"
    ),
    "Get15118EVCertificate": build_request_model(
        "Get15118EVCertificateRequest201", action="Get15118EVCertificate"
    ),
    "GetCertificateStatus": build_request_model(
        "GetCertificateStatusRequest201", action="GetCertificateStatus"
    ),
    "SignCertificate": build_request_model(
        "SignCertificateRequest201", action="SignCertificate"
    ),
    "LogStatusNotification": build_request_model(
        "LogStatusNotificationRequest201", action="LogStatusNotification"
    ),
    "TransactionEvent": build_request_model(
        "TransactionEventRequest201",
        action="TransactionEvent",
        required_fields={"eventType": str},
    ),
    "FirmwareStatusNotification": build_request_model(
        "FirmwareStatusNotificationRequest201", action="FirmwareStatusNotification"
    ),
    "ReserveNow": build_request_model("ReserveNowRequest201", action="ReserveNow"),
    "RequestStartTransaction": build_request_model(
        "RequestStartTransactionRequest201", action="RequestStartTransaction"
    ),
    "RequestStopTransaction": build_request_model(
        "RequestStopTransactionRequest201", action="RequestStopTransaction"
    ),
    "GetTransactionStatus": build_request_model(
        "GetTransactionStatusRequest201", action="GetTransactionStatus"
    ),
    "ChangeAvailability": build_request_model(
        "ChangeAvailabilityRequest201", action="ChangeAvailability"
    ),
    "ClearCache": build_request_model("ClearCacheRequest201", action="ClearCache"),
    "GetLog": build_request_model("GetLogRequest201", action="GetLog"),
    "CancelReservation": build_request_model(
        "CancelReservationRequest201", action="CancelReservation"
    ),
    "UnlockConnector": build_request_model(
        "UnlockConnectorRequest201", action="UnlockConnector"
    ),
    "Reset": build_request_model("ResetRequest201", action="Reset"),
    "TriggerMessage": build_request_model(
        "TriggerMessageRequest201", action="TriggerMessage"
    ),
    "SendLocalList": build_request_model(
        "SendLocalListRequest201", action="SendLocalList"
    ),
    "GetLocalListVersion": build_request_model(
        "GetLocalListVersionRequest201", action="GetLocalListVersion"
    ),
    "GetCompositeSchedule": build_request_model(
        "GetCompositeScheduleRequest201", action="GetCompositeSchedule"
    ),
    "UpdateFirmware": build_request_model(
        "UpdateFirmwareRequest201", action="UpdateFirmware"
    ),
    "PublishFirmware": build_request_model(
        "PublishFirmwareRequest201", action="PublishFirmware"
    ),
    "UnpublishFirmware": build_request_model(
        "UnpublishFirmwareRequest201", action="UnpublishFirmware"
    ),
    "SetChargingProfile": build_request_model(
        "SetChargingProfileRequest201", action="SetChargingProfile"
    ),
    "ClearChargingProfile": build_request_model(
        "ClearChargingProfileRequest201", action="ClearChargingProfile"
    ),
    "InstallCertificate": build_request_model(
        "InstallCertificateRequest201", action="InstallCertificate"
    ),
    "DeleteCertificate": build_request_model(
        "DeleteCertificateRequest201", action="DeleteCertificate"
    ),
    "CertificateSigned": build_request_model(
        "CertificateSignedRequest201", action="CertificateSigned"
    ),
    "GetInstalledCertificateIds": build_request_model(
        "GetInstalledCertificateIdsRequest201", action="GetInstalledCertificateIds"
    ),
    "GetVariables": build_request_model(
        "GetVariablesRequest201", action="GetVariables"
    ),
    "SetVariables": build_request_model(
        "SetVariablesRequest201", action="SetVariables"
    ),
    "ClearDisplayMessage": build_request_model(
        "ClearDisplayMessageRequest201", action="ClearDisplayMessage"
    ),
    "CustomerInformation": build_request_model(
        "CustomerInformationRequest201", action="CustomerInformation"
    ),
    "GetBaseReport": build_request_model(
        "GetBaseReportRequest201", action="GetBaseReport"
    ),
    "GetChargingProfiles": build_request_model(
        "GetChargingProfilesRequest201", action="GetChargingProfiles"
    ),
    "GetDisplayMessages": build_request_model(
        "GetDisplayMessagesRequest201", action="GetDisplayMessages"
    ),
    "GetReport": build_request_model("GetReportRequest201", action="GetReport"),
    "SetDisplayMessage": build_request_model(
        "SetDisplayMessageRequest201", action="SetDisplayMessage"
    ),
    "SetNetworkProfile": build_request_model(
        "SetNetworkProfileRequest201", action="SetNetworkProfile"
    ),
    "SetMonitoringBase": build_request_model(
        "SetMonitoringBaseRequest201", action="SetMonitoringBase"
    ),
    "SetMonitoringLevel": build_request_model(
        "SetMonitoringLevelRequest201", action="SetMonitoringLevel"
    ),
    "SetVariableMonitoring": build_request_model(
        "SetVariableMonitoringRequest201", action="SetVariableMonitoring"
    ),
    "ClearVariableMonitoring": build_request_model(
        "ClearVariableMonitoringRequest201", action="ClearVariableMonitoring"
    ),
    "GetMonitoringReport": build_request_model(
        "GetMonitoringReportRequest201", action="GetMonitoringReport"
    ),
}

RESPONSE_MODELS = {
    "BootNotification": build_response_model(
        "BootNotificationResponse201",
        action="BootNotification",
        required_fields={
            "status": (str, RegistrationStatus),
        },
    ),
    "DataTransfer": build_response_model("DataTransferResponse201", action="DataTransfer"),
    "Heartbeat": build_response_model("HeartbeatResponse201", action="Heartbeat"),
    "StatusNotification": build_response_model(
        "StatusNotificationResponse201", action="StatusNotification"
    ),
    "Authorize": build_response_model(
        "AuthorizeResponse201",
        action="Authorize",
        required_fields={
            "idTokenInfo": dict,
        },
    ),
    "MeterValues": build_response_model("MeterValuesResponse201", action="MeterValues"),
    "ClearedChargingLimit": build_response_model(
        "ClearedChargingLimitResponse201", action="ClearedChargingLimit"
    ),
    "NotifyReport": build_response_model("NotifyReportResponse201", action="NotifyReport"),
    "NotifyChargingLimit": build_response_model(
        "NotifyChargingLimitResponse201", action="NotifyChargingLimit"
    ),
    "NotifyCustomerInformation": build_response_model(
        "NotifyCustomerInformationResponse201", action="NotifyCustomerInformation"
    ),
    "NotifyDisplayMessages": build_response_model(
        "NotifyDisplayMessagesResponse201", action="NotifyDisplayMessages"
    ),
    "NotifyEVChargingNeeds": build_response_model(
        "NotifyEVChargingNeedsResponse201", action="NotifyEVChargingNeeds"
    ),
    "NotifyEVChargingSchedule": build_response_model(
        "NotifyEVChargingScheduleResponse201", action="NotifyEVChargingSchedule"
    ),
    "NotifyEvent": build_response_model("NotifyEventResponse201", action="NotifyEvent"),
    "NotifyMonitoringReport": build_response_model(
        "NotifyMonitoringReportResponse201", action="NotifyMonitoringReport"
    ),
    "PublishFirmwareStatusNotification": build_response_model(
        "PublishFirmwareStatusNotificationResponse201",
        action="PublishFirmwareStatusNotification",
    ),
    "ReportChargingProfiles": build_response_model(
        "ReportChargingProfilesResponse201", action="ReportChargingProfiles"
    ),
    "SecurityEventNotification": build_response_model(
        "SecurityEventNotificationResponse201", action="SecurityEventNotification"
    ),
    "Get15118EVCertificate": build_response_model(
        "Get15118EVCertificateResponse201", action="Get15118EVCertificate"
    ),
    "GetCertificateStatus": build_response_model(
        "GetCertificateStatusResponse201", action="GetCertificateStatus"
    ),
    "SignCertificate": build_response_model(
        "SignCertificateResponse201", action="SignCertificate"
    ),
    "LogStatusNotification": build_response_model(
        "LogStatusNotificationResponse201", action="LogStatusNotification"
    ),
    "TransactionEvent": build_response_model(
        "TransactionEventResponse201", action="TransactionEvent"
    ),
    "FirmwareStatusNotification": build_response_model(
        "FirmwareStatusNotificationResponse201", action="FirmwareStatusNotification"
    ),
    "ReserveNow": build_response_model("ReserveNowResponse201", action="ReserveNow"),
    "RequestStartTransaction": build_response_model(
        "RequestStartTransactionResponse201", action="RequestStartTransaction"
    ),
    "RequestStopTransaction": build_response_model(
        "RequestStopTransactionResponse201", action="RequestStopTransaction"
    ),
    "GetTransactionStatus": build_response_model(
        "GetTransactionStatusResponse201", action="GetTransactionStatus"
    ),
    "ChangeAvailability": build_response_model(
        "ChangeAvailabilityResponse201", action="ChangeAvailability"
    ),
    "ClearCache": build_response_model("ClearCacheResponse201", action="ClearCache"),
    "GetLog": build_response_model("GetLogResponse201", action="GetLog"),
    "CancelReservation": build_response_model(
        "CancelReservationResponse201", action="CancelReservation"
    ),
    "UnlockConnector": build_response_model(
        "UnlockConnectorResponse201", action="UnlockConnector"
    ),
    "Reset": build_response_model("ResetResponse201", action="Reset"),
    "TriggerMessage": build_response_model(
        "TriggerMessageResponse201", action="TriggerMessage"
    ),
    "SendLocalList": build_response_model(
        "SendLocalListResponse201", action="SendLocalList"
    ),
    "GetLocalListVersion": build_response_model(
        "GetLocalListVersionResponse201", action="GetLocalListVersion"
    ),
    "GetCompositeSchedule": build_response_model(
        "GetCompositeScheduleResponse201", action="GetCompositeSchedule"
    ),
    "UpdateFirmware": build_response_model(
        "UpdateFirmwareResponse201", action="UpdateFirmware"
    ),
    "PublishFirmware": build_response_model(
        "PublishFirmwareResponse201", action="PublishFirmware"
    ),
    "UnpublishFirmware": build_response_model(
        "UnpublishFirmwareResponse201", action="UnpublishFirmware"
    ),
    "SetChargingProfile": build_response_model(
        "SetChargingProfileResponse201", action="SetChargingProfile"
    ),
    "ClearChargingProfile": build_response_model(
        "ClearChargingProfileResponse201", action="ClearChargingProfile"
    ),
    "InstallCertificate": build_response_model(
        "InstallCertificateResponse201", action="InstallCertificate"
    ),
    "DeleteCertificate": build_response_model(
        "DeleteCertificateResponse201", action="DeleteCertificate"
    ),
    "CertificateSigned": build_response_model(
        "CertificateSignedResponse201", action="CertificateSigned"
    ),
    "GetInstalledCertificateIds": build_response_model(
        "GetInstalledCertificateIdsResponse201", action="GetInstalledCertificateIds"
    ),
    "GetVariables": build_response_model(
        "GetVariablesResponse201", action="GetVariables"
    ),
    "SetVariables": build_response_model(
        "SetVariablesResponse201", action="SetVariables"
    ),
    "ClearDisplayMessage": build_response_model(
        "ClearDisplayMessageResponse201", action="ClearDisplayMessage"
    ),
    "CustomerInformation": build_response_model(
        "CustomerInformationResponse201", action="CustomerInformation"
    ),
    "GetBaseReport": build_response_model(
        "GetBaseReportResponse201", action="GetBaseReport"
    ),
    "GetChargingProfiles": build_response_model(
        "GetChargingProfilesResponse201", action="GetChargingProfiles"
    ),
    "GetDisplayMessages": build_response_model(
        "GetDisplayMessagesResponse201", action="GetDisplayMessages"
    ),
    "GetReport": build_response_model("GetReportResponse201", action="GetReport"),
    "SetDisplayMessage": build_response_model(
        "SetDisplayMessageResponse201", action="SetDisplayMessage"
    ),
    "SetNetworkProfile": build_response_model(
        "SetNetworkProfileResponse201", action="SetNetworkProfile"
    ),
    "SetMonitoringBase": build_response_model(
        "SetMonitoringBaseResponse201", action="SetMonitoringBase"
    ),
    "SetMonitoringLevel": build_response_model(
        "SetMonitoringLevelResponse201", action="SetMonitoringLevel"
    ),
    "SetVariableMonitoring": build_response_model(
        "SetVariableMonitoringResponse201", action="SetVariableMonitoring"
    ),
    "ClearVariableMonitoring": build_response_model(
        "ClearVariableMonitoringResponse201", action="ClearVariableMonitoring"
    ),
    "GetMonitoringReport": build_response_model(
        "GetMonitoringReportResponse201", action="GetMonitoringReport"
    ),
}
