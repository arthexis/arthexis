from datetime import datetime, timezone

import pytest

from apps.ocpp.domain.sessions import (
    current_transaction,
    is_historical_evidence,
    last_completed_transaction,
    last_transaction,
    mark_transaction_unresolved,
)
from apps.ocpp.models import OcppTransaction
from tests.apps.ocpp.builders import charger, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def selected_charger():
    return charger("charger-1")


def test_transaction_queries_and_read_helpers_are_deterministic(
    selected_charger,
) -> None:
    first = transaction(
        selected_charger,
        "first",
        started_at=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
    )
    active_older = transaction(
        selected_charger,
        "active-older",
        started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
    )
    active_newer = transaction(
        selected_charger,
        "active-newer",
        started_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
    )
    completed_newer = transaction(
        selected_charger,
        "completed-newer",
        started_at=datetime(2026, 9, 19, 14, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 9, 19, 15, tzinfo=timezone.utc),
    )

    assert list(OcppTransaction.objects.active().recent()) == [
        active_newer,
        active_older,
    ]
    assert list(OcppTransaction.objects.completed().recent()) == [
        completed_newer,
        first,
    ]
    assert current_transaction(selected_charger) == active_newer
    assert last_transaction(selected_charger) == completed_newer
    assert last_completed_transaction(selected_charger) == completed_newer


def test_transaction_recency_uses_primary_key_to_break_timestamp_ties(
    selected_charger,
) -> None:
    timestamp = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
    older_pk = transaction(selected_charger, "tie-1", started_at=timestamp)
    newer_pk = transaction(selected_charger, "tie-2", started_at=timestamp)

    assert list(OcppTransaction.objects.recent()) == [newer_pk, older_pk]
    assert current_transaction(selected_charger) == newer_pk


def test_energy_resolution_querysets_are_independent_of_session_recovery(
    selected_charger,
) -> None:
    resolved = transaction(
        selected_charger,
        "resolved-energy",
        started_at=datetime(2026, 9, 19, 10, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 9, 19, 11, tzinfo=timezone.utc),
        energy_kwh="1.2500",
    )
    unresolved_energy = transaction(
        selected_charger,
        "unresolved-energy",
        started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
    )
    open_unresolved = transaction(
        selected_charger,
        "open-unresolved",
        started_at=datetime(2026, 9, 19, 14, tzinfo=timezone.utc),
    )
    open_unresolved.recovery_state = OcppTransaction.RecoveryState.UNRESOLVED
    open_unresolved.save(update_fields=("recovery_state",))

    assert list(OcppTransaction.objects.energy_resolved()) == [resolved]
    assert list(OcppTransaction.objects.energy_unresolved()) == [unresolved_energy]
    assert open_unresolved not in OcppTransaction.objects.energy_unresolved()


def test_authority_cutover_classification_is_strictly_before(
    selected_charger,
) -> None:
    cutover = datetime(2026, 9, 22, 14, 0, tzinfo=timezone.utc)
    selected_charger.authority_cutover_at = cutover
    selected_charger.save(update_fields=("authority_cutover_at",))

    assert is_historical_evidence(
        selected_charger,
        datetime(2026, 9, 22, 13, 59, 59, tzinfo=timezone.utc),
    )
    assert not is_historical_evidence(selected_charger, cutover)
    assert not is_historical_evidence(
        selected_charger,
        datetime(2026, 9, 22, 14, 0, 1, tzinfo=timezone.utc),
    )


def test_null_cutover_preserves_normal_semantics(selected_charger) -> None:
    assert selected_charger.authority_cutover_at is None
    assert not is_historical_evidence(
        selected_charger,
        datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


def test_historical_open_transaction_is_not_live_or_current(
    selected_charger,
) -> None:
    historical = transaction(
        selected_charger,
        "historical-open",
        started_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        historical=True,
    )

    assert list(OcppTransaction.objects.historical()) == [historical]
    assert list(OcppTransaction.objects.live()) == []
    assert list(OcppTransaction.objects.active()) == []
    assert list(OcppTransaction.objects.open()) == []
    assert current_transaction(selected_charger) is None


def test_historical_unresolved_transaction_is_not_live_unresolved(
    selected_charger,
) -> None:
    historical = transaction(
        selected_charger,
        "historical-unresolved",
        started_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        historical=True,
    )
    historical.recovery_state = OcppTransaction.RecoveryState.UNRESOLVED
    historical.save(update_fields=("recovery_state",))

    assert list(OcppTransaction.objects.unresolved()) == []
    assert list(OcppTransaction.objects.historical()) == [historical]


def test_transaction_read_helpers_return_none_without_transactions(
    selected_charger,
) -> None:
    assert current_transaction(selected_charger) is None
    assert last_transaction(selected_charger) is None
    assert last_completed_transaction(selected_charger) is None


def test_unresolved_transaction_is_open_but_not_current(selected_charger) -> None:
    selected = transaction(
        selected_charger,
        "recovery-gap",
        started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
    )

    mark_transaction_unresolved(
        selected,
        observed_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
    assert selected.last_activity_at == datetime(
        2026, 9, 19, 13, tzinfo=timezone.utc
    )
    assert list(OcppTransaction.objects.active()) == []
    assert list(OcppTransaction.objects.unresolved()) == [selected]
    assert list(OcppTransaction.objects.open()) == [selected]
    assert current_transaction(selected_charger) is None


def test_completed_transaction_cannot_be_marked_unresolved(
    selected_charger,
) -> None:
    selected = transaction(
        selected_charger,
        "done",
        started_at=datetime(2026, 9, 19, 12, tzinfo=timezone.utc),
        stopped_at=datetime(2026, 9, 19, 13, tzinfo=timezone.utc),
    )

    with pytest.raises(
        ValueError,
        match="Completed transactions cannot become unresolved",
    ):
        mark_transaction_unresolved(selected)


def test_retains_standalone_meter_payload_and_timestamp() -> None:
    selected = charger("meter-batch")
    batch = selected.meter_reading_batches.create(
        protocol="ocpp2.0.1",
        evse_id=3,
        reported_at=datetime(2026, 9, 22, 12, tzinfo=timezone.utc),
        payload={"evseId": 3, "meterValue": []},
    )

    assert batch.charger == selected
    assert batch.protocol == "ocpp2.0.1"
    assert batch.evse_id == 3
    assert batch.reported_at == datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
