from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync

from apps.events.models import EventEnvelope
from apps.ocpp.domain.sessions import (
    reconcile_pending_transaction_energy,
    record_meter_values,
)
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.protocol.v201.inbound import InboundActions as Inbound201Actions
from apps.ocpp.subscribers import process_meter_values_received
from apps.ocpp.tasks import reconcile_meter_energy
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db(transaction=True, reset_sequences=True)


@pytest.fixture
def meter_context():
    selected = charger("async-meter")
    transaction = OcppTransaction.objects.create(
        charger=selected,
        remote_id="async-meter-remote",
        started_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 9, 22, 10, tzinfo=timezone.utc),
    )
    return selected, transaction


def meter_values() -> list[dict[str, object]]:
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


def dispatcher(selected, version: ProtocolVersion) -> FrameDispatcher:
    handlers = (
        InboundActions(selected)
        if version == ProtocolVersion.OCPP_16
        else Inbound201Actions(selected)
    )
    return FrameDispatcher(
        charger=selected,
        version=version,
        pending_calls=PendingCalls(),
        handler_resolver=handlers.resolve,
    )


def test_v16_ack_commits_samples_and_freshness_before_async_energy(
    meter_context,
) -> None:
    selected, transaction = meter_context
    response = async_to_sync(dispatcher(selected, ProtocolVersion.OCPP_16).dispatch)(
        Call(
            unique_id="meter-v16",
            action="MeterValues",
            payload={
                "transactionId": transaction.pk,
                "meterValue": meter_values(),
            },
        )
    )

    assert response == CallResult(unique_id="meter-v16", payload={})
    assert MeterValue.objects.count() == 2

    transaction.refresh_from_db()
    assert transaction.last_activity_at == datetime(
        2026, 9, 22, 10, 10, tzinfo=timezone.utc
    )
    assert transaction.energy_kwh is None
    assert transaction.meter_evidence_revision == 1
    assert transaction.energy_derived_revision == 0

    replay = InboundProtocolRequest.objects.get(
        charger=selected,
        action="MeterValues",
        unique_id="meter-v16",
    )
    assert replay.status == InboundProtocolRequest.Status.COMPLETED

    event = EventEnvelope.objects.get(event_type="ocpp.meter_values.received")
    assert event.payload["transaction_id"] == transaction.pk
    assert event.payload["meter_batch_id"] is None

    process_meter_values_received(event)
    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.0500")
    assert transaction.energy_derived_revision == 1


def test_v201_event_uses_local_transaction_identity(meter_context) -> None:
    selected, transaction = meter_context
    response = async_to_sync(dispatcher(selected, ProtocolVersion.OCPP_201).dispatch)(
        Call(
            unique_id="meter-v201",
            action="MeterValues",
            payload={
                "evseId": 1,
                "meterValue": meter_values(),
                "transactionInfo": {"transactionId": transaction.remote_id},
            },
        )
    )

    assert response == CallResult(unique_id="meter-v201", payload={})
    event = EventEnvelope.objects.get(event_type="ocpp.meter_values.received")
    assert event.payload["transaction_id"] == transaction.pk
    transaction.refresh_from_db()
    assert transaction.meter_evidence_revision == 1
    assert transaction.energy_derived_revision == 0


def test_meter_subscriber_is_idempotent(meter_context) -> None:
    selected, transaction = meter_context
    MeterValue.objects.create(
        transaction=transaction,
        sampled_at=datetime(2026, 9, 22, 10, 5, tzinfo=timezone.utc),
        value=Decimal("1"),
        unit="kWh",
        source_fingerprint="first",
    )
    MeterValue.objects.create(
        transaction=transaction,
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
            "charger_id": selected.pk,
            "transaction_id": transaction.pk,
            "evse_id": None,
        },
    )

    process_meter_values_received(event)
    process_meter_values_received(event)

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.5000")


def test_insufficient_samples_do_not_clear_existing_energy(meter_context) -> None:
    selected, transaction = meter_context
    transaction.energy_kwh = Decimal("2.5000")
    transaction.meter_evidence_revision = 1
    transaction.save(update_fields=("energy_kwh", "meter_evidence_revision"))
    MeterValue.objects.create(
        transaction=transaction,
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
            "charger_id": selected.pk,
            "transaction_id": transaction.pk,
            "evse_id": None,
        },
    )

    process_meter_values_received(event)

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("2.5000")
    assert transaction.energy_derived_revision == 1


def test_duplicate_retained_meter_evidence_does_not_advance_revision(
    meter_context,
) -> None:
    selected, transaction = meter_context
    first_count = record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )
    transaction.refresh_from_db()
    assert first_count == 2
    assert transaction.meter_evidence_revision == 1

    duplicate_count = record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )
    transaction.refresh_from_db()
    assert duplicate_count == 0
    assert transaction.meter_evidence_revision == 1


def test_late_older_meter_evidence_still_advances_revision(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )
    transaction.refresh_from_db()
    assert transaction.meter_evidence_revision == 1

    count = record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
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

    transaction.refresh_from_db()
    assert count == 1
    assert transaction.meter_evidence_revision == 2
    assert transaction.last_activity_at == datetime(
        2026, 9, 22, 10, 10, tzinfo=timezone.utc
    )


def test_reconciler_repairs_dirty_energy_without_event(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )
    transaction.refresh_from_db()
    assert transaction.meter_evidence_revision == 1
    assert transaction.energy_derived_revision == 0
    assert transaction.energy_kwh is None

    assert reconcile_pending_transaction_energy() == 1

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.0500")
    assert transaction.energy_derived_revision == 1


def test_reconciler_is_idempotent_after_watermark_catches_up(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )

    assert reconcile_pending_transaction_energy() == 1
    assert reconcile_pending_transaction_energy() == 0

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.0500")
    assert transaction.energy_derived_revision == transaction.meter_evidence_revision


def test_reconciler_marks_insufficient_evidence_as_considered(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:05:00Z",
                "sampledValue": [
                    {
                        "value": "100",
                        "measurand": "Energy.Active.Import.Register",
                        "unit": "Wh",
                    }
                ],
            }
        ],
    )

    assert reconcile_pending_transaction_energy() == 1

    transaction.refresh_from_db()
    assert transaction.energy_kwh is None
    assert transaction.meter_evidence_revision == 1
    assert transaction.energy_derived_revision == 1
    assert reconcile_pending_transaction_energy() == 0


def test_reconciler_respects_batch_limit(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )
    second = OcppTransaction.objects.create(
        charger=selected,
        remote_id="async-meter-second",
        started_at=datetime(2026, 9, 22, 11, tzinfo=timezone.utc),
        last_activity_at=datetime(2026, 9, 22, 11, tzinfo=timezone.utc),
    )
    record_meter_values(
        transaction_id=second.pk,
        charger=selected,
        meter_values=meter_values(),
    )

    assert reconcile_pending_transaction_energy(limit=1) == 1
    assert OcppTransaction.objects.energy_derivation_pending().count() == 1
    assert reconcile_pending_transaction_energy(limit=1) == 1
    assert not OcppTransaction.objects.energy_derivation_pending().exists()


def test_periodic_task_uses_sql_reconciler(meter_context) -> None:
    selected, transaction = meter_context
    record_meter_values(
        transaction_id=transaction.pk,
        charger=selected,
        meter_values=meter_values(),
    )

    assert reconcile_meter_energy() == 1

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.0500")
    assert transaction.energy_derived_revision == 1


@pytest.mark.parametrize(
    ("version", "unique_id"),
    [
        (ProtocolVersion.OCPP_16, "meter-event-failure-v16"),
        (ProtocolVersion.OCPP_201, "meter-event-failure-v201"),
    ],
)
def test_lost_post_commit_event_is_repaired_from_sql(
    meter_context,
    version: ProtocolVersion,
    unique_id: str,
) -> None:
    selected, transaction = meter_context
    payload = {
        "meterValue": meter_values(),
    }
    if version == ProtocolVersion.OCPP_16:
        payload["transactionId"] = transaction.pk
    else:
        payload["evseId"] = 1
        payload["transactionInfo"] = {"transactionId": transaction.remote_id}

    with patch(
        "apps.ocpp.services.transactions.publish_safely",
        side_effect=RuntimeError("event unavailable"),
    ):
        response = async_to_sync(dispatcher(selected, version).dispatch)(
            Call(
                unique_id=unique_id,
                action="MeterValues",
                payload=payload,
            )
        )

    assert response == CallResult(unique_id=unique_id, payload={})
    assert MeterValue.objects.count() == 2
    assert not EventEnvelope.objects.exists()

    replay = InboundProtocolRequest.objects.get(
        charger=selected,
        action="MeterValues",
        unique_id=unique_id,
    )
    assert replay.status == InboundProtocolRequest.Status.COMPLETED
    assert replay.response_payload == {}

    transaction.refresh_from_db()
    assert transaction.energy_kwh is None
    assert transaction.meter_evidence_revision == 1
    assert transaction.energy_derived_revision == 0

    assert reconcile_pending_transaction_energy() == 1

    transaction.refresh_from_db()
    assert transaction.energy_kwh == Decimal("0.0500")
    assert transaction.energy_derived_revision == 1
    assert not EventEnvelope.objects.exists()
