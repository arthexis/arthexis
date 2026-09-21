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
from apps.ocpp.domain.sessions import (
    current_transaction,
    last_completed_transaction,
    last_transaction,
)
from apps.ocpp.models import (
    CertificateRecord,
    Charger,
    ChargerVariable,
    ChargingProfile,
    MonitoringRecord,
    NotificationRecord,
    OcppTransaction,
    OperationalStatusRecord,
    ProtocolOperation,
    Reservation,
)
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from tests.ocpp.builders import charger, connection, transaction


class OcppPersistenceTests(TestCase):
    def setUp(self) -> None:
        self.charger = charger("charger-1")

    def test_charger_uses_identity_as_its_natural_key(self) -> None:
        self.assertEqual(self.charger.natural_key(), ("charger-1",))
        self.assertEqual(
            Charger.objects.get_by_natural_key("charger-1"),
            self.charger,
        )

    def test_charger_queryset_separates_enabled_connected_and_charging_state(
        self,
    ) -> None:
        disabled = charger("charger-disabled", active=False)
        connected_idle = charger("charger-idle")
        connected_charging = charger("charger-charging")
        connection(connected_idle, channel_name="idle-channel", protocol="ocpp1.6")
        connection(
            connected_charging,
            channel_name="charging-channel",
            protocol="ocpp2.0.1",
        )
        transaction(
            connected_charging,
            "active-transaction",
            started_at=datetime(2026, 9, 19, tzinfo=UTC),
        )

        self.assertQuerySetEqual(
            Charger.objects.enabled().order_by("identity"),
            [self.charger, connected_charging, connected_idle],
        )
        self.assertQuerySetEqual(
            Charger.objects.disabled(),
            [disabled],
        )
        self.assertQuerySetEqual(
            Charger.objects.connected().order_by("identity"),
            [connected_charging, connected_idle],
        )
        self.assertQuerySetEqual(
            Charger.objects.disconnected().order_by("identity"),
            [self.charger, disabled],
        )
        self.assertQuerySetEqual(
            Charger.objects.charging(),
            [connected_charging],
        )
        self.assertQuerySetEqual(
            Charger.objects.idle(),
            [connected_idle],
        )

    def test_charging_requires_an_actual_active_transaction(self) -> None:
        connected_without_transactions = charger("charger-idle")
        connection(connected_without_transactions, channel_name="idle-channel")

        self.assertNotIn(self.charger, Charger.objects.charging())
        self.assertNotIn(connected_without_transactions, Charger.objects.charging())
        self.assertIn(connected_without_transactions, Charger.objects.idle())

    def test_transaction_queries_and_read_helpers_are_deterministic(self) -> None:
        first = transaction(
            self.charger,
            "first",
            started_at=datetime(2026, 9, 19, 10, tzinfo=UTC),
            stopped_at=datetime(2026, 9, 19, 11, tzinfo=UTC),
        )
        active_older = transaction(
            self.charger,
            "active-older",
            started_at=datetime(2026, 9, 19, 12, tzinfo=UTC),
        )
        active_newer = transaction(
            self.charger,
            "active-newer",
            started_at=datetime(2026, 9, 19, 13, tzinfo=UTC),
        )
        completed_newer = transaction(
            self.charger,
            "completed-newer",
            started_at=datetime(2026, 9, 19, 14, tzinfo=UTC),
            stopped_at=datetime(2026, 9, 19, 15, tzinfo=UTC),
        )

        self.assertQuerySetEqual(
            OcppTransaction.objects.active().recent(),
            [active_newer, active_older],
        )
        self.assertQuerySetEqual(
            OcppTransaction.objects.completed().recent(),
            [completed_newer, first],
        )
        self.assertEqual(current_transaction(self.charger), active_newer)
        self.assertEqual(last_transaction(self.charger), completed_newer)
        self.assertEqual(
            last_completed_transaction(self.charger),
            completed_newer,
        )

    def test_transaction_recency_uses_primary_key_to_break_timestamp_ties(
        self,
    ) -> None:
        timestamp = datetime(2026, 9, 19, 12, tzinfo=UTC)
        older_pk = transaction(self.charger, "tie-1", started_at=timestamp)
        newer_pk = transaction(self.charger, "tie-2", started_at=timestamp)

        self.assertEqual(
            list(OcppTransaction.objects.recent()),
            [newer_pk, older_pk],
        )
        self.assertEqual(current_transaction(self.charger), newer_pk)

    def test_transaction_read_helpers_return_none_without_transactions(self) -> None:
        self.assertIsNone(current_transaction(self.charger))
        self.assertIsNone(last_transaction(self.charger))
        self.assertIsNone(last_completed_transaction(self.charger))

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
