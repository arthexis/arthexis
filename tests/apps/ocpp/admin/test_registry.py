from django.contrib import admin

from apps.ocpp.models import (
    CertificateRecord,
    ChargerVariable,
    ChargingProfile,
    MonitoringRecord,
    NotificationRecord,
    OperationalStatusRecord,
    ProtocolOperation,
    Reservation,
)


def test_retained_models_are_registered_in_admin() -> None:
    for model in (
        CertificateRecord,
        ChargerVariable,
        ChargingProfile,
        MonitoringRecord,
        NotificationRecord,
        OperationalStatusRecord,
        ProtocolOperation,
        Reservation,
    ):
        assert model in admin.site._registry
