from django.contrib import admin
from django.contrib.admin.sites import NotRegistered

from ...models import (
    CertificateOperation,
    CertificateRequest,
    CertificateStatusCheck,
    Charger,
    ChargerConfiguration,
    ChargerLogRequest,
    ChargingProfile,
    ConfigurationKey,
    ControlOperationEvent,
    CPFirmware,
    CPFirmwareDeployment,
    CPFirmwareRequest,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPReservation,
    CustomerInformationChunk,
    CustomerInformationRequest,
    DataTransferMessage,
    DisplayMessage,
    DisplayMessageNotification,
    InstalledCertificate,
    MeterValue,
    PowerProjection,
    SecurityEvent,
    StationModel,
    Transaction,
    TrustAnchor,
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
    Transaction,
    MeterValue,
    SecurityEvent,
    ChargerLogRequest,
    StationModel,
    CPNetworkProfile,
    CPNetworkProfileDeployment,
    CPFirmwareRequest,
    CertificateRequest,
    CertificateStatusCheck,
    CertificateOperation,
    InstalledCertificate,
    TrustAnchor,
    CustomerInformationRequest,
    CustomerInformationChunk,
    DisplayMessageNotification,
    DisplayMessage,
    ControlOperationEvent,
):
    try:
        admin.site.unregister(_model)
    except NotRegistered:
        pass

from . import (  # noqa: E402,F401
    certificates_admin,
    core_admin,
    firmware_admin,
    messages_admin,
    network_profiles_admin,
    transactions_admin,
)

__all__ = [
    "certificates_admin",
    "core_admin",
    "firmware_admin",
    "messages_admin",
    "network_profiles_admin",
    "transactions_admin",
]
