from __future__ import annotations

from .base import build_request_model, build_response_model


REQUEST_MODELS = {
    "NotifyReport": build_request_model("NotifyReportRequest21", action="NotifyReport"),
    "ClearedChargingLimit": build_request_model(
        "ClearedChargingLimitRequest21", action="ClearedChargingLimit"
    ),
    "CostUpdated": build_request_model("CostUpdatedRequest21", action="CostUpdated"),
    "ReservationStatusUpdate": build_request_model(
        "ReservationStatusUpdateRequest21", action="ReservationStatusUpdate"
    ),
    "Get15118EVCertificate": build_request_model(
        "Get15118EVCertificateRequest21", action="Get15118EVCertificate"
    ),
    "GetCertificateStatus": build_request_model(
        "GetCertificateStatusRequest21", action="GetCertificateStatus"
    ),
    "SignCertificate": build_request_model(
        "SignCertificateRequest21", action="SignCertificate"
    ),
    "UpdateFirmware": build_request_model(
        "UpdateFirmwareRequest21", action="UpdateFirmware"
    ),
    "PublishFirmware": build_request_model(
        "PublishFirmwareRequest21", action="PublishFirmware"
    ),
    "UnpublishFirmware": build_request_model(
        "UnpublishFirmwareRequest21", action="UnpublishFirmware"
    ),
}

RESPONSE_MODELS = {
    "NotifyReport": build_response_model("NotifyReportResponse21", action="NotifyReport"),
    "ClearedChargingLimit": build_response_model(
        "ClearedChargingLimitResponse21", action="ClearedChargingLimit"
    ),
    "CostUpdated": build_response_model("CostUpdatedResponse21", action="CostUpdated"),
    "ReservationStatusUpdate": build_response_model(
        "ReservationStatusUpdateResponse21", action="ReservationStatusUpdate"
    ),
    "Get15118EVCertificate": build_response_model(
        "Get15118EVCertificateResponse21", action="Get15118EVCertificate"
    ),
    "GetCertificateStatus": build_response_model(
        "GetCertificateStatusResponse21", action="GetCertificateStatus"
    ),
    "SignCertificate": build_response_model(
        "SignCertificateResponse21", action="SignCertificate"
    ),
    "UpdateFirmware": build_response_model(
        "UpdateFirmwareResponse21", action="UpdateFirmware"
    ),
    "PublishFirmware": build_response_model(
        "PublishFirmwareResponse21", action="PublishFirmware"
    ),
    "UnpublishFirmware": build_response_model(
        "UnpublishFirmwareResponse21", action="UnpublishFirmware"
    ),
}
