from datetime import UTC, datetime

from django.contrib import admin
from django.test import TestCase

from apps.ocpp.domain.configuration import record_variable
from apps.ocpp.domain.notifications import (
    record_certificate,
    record_monitoring,
    record_notification,
    record_operational_status,
)
from apps.ocpp.domain.operations import complete_operation, create_operation
from apps.ocpp.domain.profiles import record_profile
from apps.ocpp.domain.reservations import record_reservation
from apps.ocpp.models import (
    CertificateRecord,
    Charger,
    ChargerVariable,
    ChargingProfile,
    MonitoringRecord,
    NotificationRecord,
    OperationalStatusRecord,
    ProtocolOperation,
    Reservation,
)
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion


class OcppPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.charger = Charger.objects.create(identity="charger-1")

    def test_matrix_records_are_owned_by_small_domain_services(self) -> None:
        operation = create_operation(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action="GetVariables",
            request_payload={"getVariableData": []},
        )
        complete_operation(operation, response_payload={"getVariableResult": []})
        variable = record_variable(
            charger=self.charger,
            component="EVSE",
            variable="AvailabilityState",
            attribute_type="Actual",
            value="Available",
            mutable=False,
        )
        profile = record_profile(
            charger=self.charger,
            remote_id="profile-1",
            purpose="TxDefaultProfile",
            kind="Absolute",
            payload={"chargingSchedule": {}},
        )
        reservation = record_reservation(
            charger=self.charger,
            remote_id="reservation-1",
            id_tag="card-1",
            expires_at=datetime(2026, 1, 1, tzinfo=UTC),
        )

        self.assertEqual(operation.status, ProtocolOperation.Status.COMPLETED)
        self.assertEqual(variable.value, "Available")
        self.assertTrue(profile.active)
        self.assertEqual(reservation.status, "pending")

    def test_notification_certificate_and_status_records_do_not_store_secrets(
        self,
    ) -> None:
        notification = record_notification(
            charger=self.charger,
            action="NotifyEvent",
            payload={"eventData": []},
        )
        monitoring = record_monitoring(
            charger=self.charger,
            event_type="Threshold",
            payload={"value": 3},
            severity=2,
        )
        certificate = record_certificate(
            charger=self.charger,
            fingerprint="sha256:example",
            certificate_type="ChargingStationCertificate",
        )
        status = record_operational_status(
            charger=self.charger,
            kind=OperationalStatusRecord.Kind.FIRMWARE,
            status="Downloaded",
            payload={},
        )

        self.assertEqual(notification.action, "NotifyEvent")
        self.assertEqual(monitoring.severity, 2)
        self.assertEqual(certificate.fingerprint, "sha256:example")
        self.assertEqual(status.kind, OperationalStatusRecord.Kind.FIRMWARE)

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
