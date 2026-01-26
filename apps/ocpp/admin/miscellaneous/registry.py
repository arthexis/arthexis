from django.contrib import admin
from django.contrib.admin.sites import NotRegistered

from ...models import (
    ChargerConfiguration,
    ConfigurationKey,
    DataTransferMessage,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    ChargingProfile,
    CPReservation,
    PowerProjection,
    Charger,
    Simulator,
    Transaction,
    MeterValue,
    SecurityEvent,
    ChargerLogRequest,
    CPForwarder,
    StationModel,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    RFIDSessionAttempt,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
)


# Ensure admin reloads (e.g., in tests) do not fail due to existing registrations.
for _model in (
    ChargerConfiguration,
    ConfigurationKey,
    DataTransferMessage,
    CPFirmware,
    CPFirmwareDeployment,
    ChargingProfile,
    CPReservation,
    PowerProjection,
    Charger,
    Simulator,
    Transaction,
    MeterValue,
    SecurityEvent,
    ChargerLogRequest,
    CPForwarder,
    StationModel,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPFirmwareRequest,
    RFIDSessionAttempt,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
):
    try:
        admin.site.unregister(_model)
    except NotRegistered:
        pass

from . import (  # noqa: E402,F401
    core_admin,
    firmware_admin,
    network_profiles_admin,
    certificates_admin,
    transactions_admin,
    simulator_admin,
)

__all__ = [
    "core_admin",
    "firmware_admin",
    "network_profiles_admin",
    "certificates_admin",
    "transactions_admin",
    "simulator_admin",
]
