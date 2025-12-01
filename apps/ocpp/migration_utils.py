"""Migration-safe helpers for the OCPP app."""

import secrets

OCPP_FORWARDING_MESSAGES = (
    "Authorize",
    "BootNotification",
    "ChangeAvailability",
    "ChangeConfiguration",
    "ClearCache",
    "ClearChargingProfile",
    "ClearDisplayMessage",
    "ClearVariableMonitoring",
    "DataTransfer",
    "DeleteCertificate",
    "FirmwareStatusNotification",
    "Get15118EVCertificate",
    "GetBaseReport",
    "GetCertificateStatus",
    "GetChargingProfiles",
    "GetCompositeSchedule",
    "GetDiagnostics",
    "GetDisplayMessages",
    "GetInstalledCertificateIds",
    "GetLocalListVersion",
    "GetLog",
    "GetMonitoringReport",
    "GetReport",
    "GetTransactionStatus",
    "Heartbeat",
    "InstallCertificate",
    "LogStatusNotification",
    "MeterValues",
    "NotifyChargingLimit",
    "NotifyCustomerInformation",
    "NotifyDisplayMessages",
    "NotifyEVChargingSchedule",
    "NotifyEVChargingNeeds",
    "NotifyEvent",
    "NotifyMonitoringReport",
    "NotifyReport",
    "PublishFirmwareStatusNotification",
    "ReportChargingProfiles",
    "ReservationStatusUpdate",
    "SecurityEventNotification",
    "SignCertificate",
    "TransactionEvent",
)


def generate_log_request_id() -> int:
    """Return a random positive identifier suitable for OCPP log requests."""

    return secrets.randbits(31) or 1


def default_forwarded_messages() -> list[str]:
    """Default set of forwarded messages used by migrations."""

    return list(OCPP_FORWARDING_MESSAGES)
