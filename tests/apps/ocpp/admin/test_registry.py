from django.contrib import admin
from django.test import SimpleTestCase

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


class OcppAdminRegistryTests(SimpleTestCase):
    def test_retained_models_are_registered_in_admin(self) -> None:
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
            self.assertIn(model, admin.site._registry)
