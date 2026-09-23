from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase

from apps.events.models import EventEnvelope
from apps.ocpp.domain.sessions import record_meter_values
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.subscribers import process_meter_values_received
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger


class AsyncMeterDerivationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.charger = charger("async-meter")
        self.transaction = OcppTransaction.objects.create(
            charger=self.charger,
            remote_id="async-meter-remote",
            started_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
            last_activity_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
        )

    @staticmethod
    def _meter_values() -> list[dict[str, object]]:
        return [
            {
                "timestamp": "2026-09-22T10:05:00Z",
                "sampledValue": [
                    {
                        "value": "100",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    }
                ],
            },
            {
                "timestamp": "2026-09-22T10:10:00Z",
                "sampledValue": [
                    {
                        "value": "150",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    }
                ],
            },
        ]

    def _v16_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _v201_dispatcher(self) -> FrameDispatcher:
        return FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_201,
            pending_calls=PendingCalls(),
            handler_resolver=Inbound201Actions(self.charger).resolve,
        )

    def test_v16_ack_commits_samples_and_freshness_before_async_energy(self) -> None:
        response = async_to_sync(self._v16_dispatcher().dispatch)(
            Call(
                unique_id="meter-v16",
                action="MeterValues",
                payload={
                    "transactionId": self.transaction.pk,
                    "meterValue": self._meter_values(),
                },
            )
        )

        self.assertEqual(response, CallResult(unique_id="meter-v16", payload={}))
        self.assertEqual(MeterValue.objects.count(), 2)

        self.transaction.refresh_from_db()
        self.assertEqual(
            self.transaction.last_activity_at,
            datetime(2026, 9, 22, 10, 10, tzinfo=timezone.utc),
        )
        self.assertIsNone(self.transaction.energy_kwh)
        self.assertEqual(self.transaction.meter_evidence_revision, 1)
        self.assertEqual(self.transaction.energy_derived_revision, 0)

        replay = InboundProtocolRequest.objects.get(
            charger=self.charger,
            action="MeterValues",
            unique_id="meter-v16",
        )
        self.assertEqual(replay.status, InboundProtocolRequest.Status.COMPLETED)

        event = EventEnvelope.objects.get(event_type="ocpp.meter_values.received")
        self.assertEqual(event.payload["transaction_id"], self.transaction.pk)
        self.assertIsNone(event.payload["meter_batch_id"])

        process_meter_values_received(event)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.energy_kwh, Decimal("0.0500"))
        self.assertEqual(self.transaction.energy_derived_revision, 1)

    def test_v201_event_uses_local_transaction_identity(self) -> None:
        response = async_to_sync(self._v201_dispatcher().dispatch)(
            Call(
                unique_id="meter-v201",
                action="MeterValues",
                payload={
                    "evseId": 1,
                    "meterValue": self._meter_values(),
                    "transactionInfo": {
                        "transactionId": self.transaction.remote_id,
                    },
                },
            )
        )

        self.assertEqual(response, CallResult(unique_id="meter-v201", payload={}))
        event = EventEnvelope.objects.get(event_type="ocpp.meter_values.received")
        self.assertEqual(event.payload["transaction_id"], self.transaction.pk)
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.meter_evidence_revision, 1)
        self.assertEqual(self.transaction.energy_derived_revision, 0)

    def test_meter_subscriber_is_idempotent(self) -> None:
        MeterValue.objects.create(
            transaction=self.transaction,
            sampled_at=datetime(2026, 9, 22, 10, 5, tzinfo=timezone.utc),
            value=Decimal("1"),
            unit="kWh",
            source_fingerprint="first",
        )
        MeterValue.objects.create(
            transaction=self.transaction,
            sampled_at=datetime(2026, 9, 22, 10, 10, tzinfo=timezone.utc),
            value=Decimal("1.5"),
            unit="kWh",
            source_fingerprint="second",
        )
        event = EventEnvelope.objects.create(
            event_type="ocpp.meter_values.received",
            producer="ocpp",
            payload={
                "meter_batch_id": None,
                "charger_id": self.charger.pk,
                "transaction_id": self.transaction.pk,
                "evse_id": None,
            },
        )

        process_meter_values_received(event)
        process_meter_values_received(event)

        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.energy_kwh, Decimal("0.5000"))

    def test_insufficient_samples_do_not_clear_existing_energy(self) -> None:
        self.transaction.energy_kwh = Decimal("2.5000")
        self.transaction.meter_evidence_revision = 1
        self.transaction.save(
            update_fields=("energy_kwh", "meter_evidence_revision")
        )
        MeterValue.objects.create(
            transaction=self.transaction,
            sampled_at=datetime(2026, 9, 22, 10, 5, tzinfo=timezone.utc),
            value=Decimal("100"),
            unit="Wh",
            source_fingerprint="only",
        )
        event = EventEnvelope.objects.create(
            event_type="ocpp.meter_values.received",
            producer="ocpp",
            payload={
                "meter_batch_id": None,
                "charger_id": self.charger.pk,
                "transaction_id": self.transaction.pk,
                "evse_id": None,
            },
        )

        process_meter_values_received(event)

        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.energy_kwh, Decimal("2.5000"))
        self.assertEqual(self.transaction.energy_derived_revision, 1)

    def test_duplicate_retained_meter_evidence_does_not_advance_revision(self) -> None:
        first_count = record_meter_values(
            transaction_id=self.transaction.pk,
            charger=self.charger,
            meter_values=self._meter_values(),
        )
        self.transaction.refresh_from_db()
        self.assertEqual(first_count, 2)
        self.assertEqual(self.transaction.meter_evidence_revision, 1)

        duplicate_count = record_meter_values(
            transaction_id=self.transaction.pk,
            charger=self.charger,
            meter_values=self._meter_values(),
        )
        self.transaction.refresh_from_db()
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(self.transaction.meter_evidence_revision, 1)

    def test_late_older_meter_evidence_still_advances_revision(self) -> None:
        record_meter_values(
            transaction_id=self.transaction.pk,
            charger=self.charger,
            meter_values=self._meter_values(),
        )
        self.transaction.refresh_from_db()
        self.assertEqual(self.transaction.meter_evidence_revision, 1)

        count = record_meter_values(
            transaction_id=self.transaction.pk,
            charger=self.charger,
            meter_values=[
                {
                    "timestamp": "2026-09-22T09:55:00Z",
                    "sampledValue": [
                        {
                            "value": "50",
                            "measurand": "Energy.Active.Import.Register",
                            "unit": "Wh",
                        }
                    ],
                }
            ],
        )

        self.transaction.refresh_from_db()
        self.assertEqual(count, 1)
        self.assertEqual(self.transaction.meter_evidence_revision, 2)
        self.assertEqual(
            self.transaction.last_activity_at,
            datetime(2026, 9, 22, 10, 10, tzinfo=timezone.utc),
        )

    def test_secondary_meter_event_failure_does_not_change_ack(self) -> None:
        with patch(
            "apps.ocpp.services.transactions.publish_safely",
            side_effect=RuntimeError("event unavailable"),
        ):
            response = async_to_sync(self._v16_dispatcher().dispatch)(
                Call(
                    unique_id="meter-event-failure",
                    action="MeterValues",
                    payload={
                        "transactionId": self.transaction.pk,
                        "meterValue": self._meter_values(),
                    },
                )
            )

        self.assertEqual(
            response,
            CallResult(unique_id="meter-event-failure", payload={}),
        )
        self.assertEqual(MeterValue.objects.count(), 2)
        self.assertFalse(EventEnvelope.objects.exists())
        self.transaction.refresh_from_db()
        self.assertIsNone(self.transaction.energy_kwh)
        self.assertEqual(self.transaction.meter_evidence_revision, 1)
        self.assertEqual(self.transaction.energy_derived_revision, 0)
