from datetime import datetime, timezone

import pytest

from apps.ocpp.domain.sessions import (
    clear_stale_charger_state,
    reconcile_connector_status,
    record_meter_values,
    record_v201_transaction_event,
    start_transaction,
    stop_transaction,
)
from apps.ocpp.models import OcppTransaction
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


@pytest.fixture
def recovery_charger():
    return charger("recovery-domain")


def test_v16_lifecycle_tracks_activity_and_completion_state(recovery_charger) -> None:
    started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    meter_at = datetime(2026, 9, 22, 10, 15, tzinfo=timezone.utc)
    stopped_at = datetime(2026, 9, 22, 10, 30, tzinfo=timezone.utc)

    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp=started_at.isoformat(),
    )
    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.last_activity_at == started_at

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": meter_at.isoformat(),
                "sampledValue": [{"value": "125"}],
            }
        ],
    )
    selected.refresh_from_db()
    assert selected.last_activity_at == meter_at

    stop_transaction(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_stop=150,
        timestamp=stopped_at.isoformat(),
    )
    selected.refresh_from_db()
    assert selected.recovery_state == OcppTransaction.RecoveryState.COMPLETED
    assert selected.last_activity_at == stopped_at
    assert selected.stopped_at == stopped_at


def test_absence_of_new_meter_evidence_does_not_end_or_unresolve_session(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )

    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.stopped_at is None
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, tzinfo=timezone.utc
    )


def test_older_meter_sample_does_not_move_activity_backwards(recovery_charger) -> None:
    started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp=started_at.isoformat(),
    )

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": datetime(
                    2026, 9, 22, 9, 55, tzinfo=timezone.utc
                ).isoformat(),
                "sampledValue": [{"value": "95"}],
            }
        ],
    )
    selected.refresh_from_db()

    assert selected.last_activity_at == started_at


def test_v201_ended_event_marks_transaction_completed(recovery_charger) -> None:
    started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    ended_at = datetime(2026, 9, 22, 11, tzinfo=timezone.utc)

    selected = record_v201_transaction_event(
        charger=recovery_charger,
        event_type="Started",
        transaction_id="remote-1",
        id_token="card",
        evse_id=1,
        connector_id=1,
        timestamp=started_at.isoformat(),
    )
    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE

    record_v201_transaction_event(
        charger=recovery_charger,
        event_type="Ended",
        transaction_id="remote-1",
        id_token="",
        evse_id=1,
        connector_id=1,
        timestamp=ended_at.isoformat(),
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.COMPLETED
    assert selected.last_activity_at == ended_at
    assert selected.stopped_at == ended_at


def test_available_status_marks_matching_active_transaction_unresolved(
    recovery_charger,
) -> None:
    started_at = datetime(2026, 9, 22, 10, tzinfo=timezone.utc)
    observed_at = datetime(2026, 9, 22, 10, 20, tzinfo=timezone.utc)
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp=started_at.isoformat(),
    )

    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at=observed_at.isoformat(),
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
    assert selected.stopped_at is None
    assert selected.last_activity_at == observed_at


def test_older_available_status_does_not_override_newer_meter_evidence(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:30:00Z",
                "sampledValue": [{"value": "130"}],
            }
        ],
    )

    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at="2026-09-22T10:20:00Z",
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, 30, tzinfo=timezone.utc
    )


def test_equal_time_available_status_does_not_override_meter_evidence(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:20:00Z",
                "sampledValue": [{"value": "125"}],
            }
        ],
    )

    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at="2026-09-22T10:20:00Z",
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE


def test_newer_available_status_marks_transaction_unresolved(recovery_charger) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:20:00Z",
                "sampledValue": [{"value": "125"}],
            }
        ],
    )

    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at="2026-09-22T10:21:00Z",
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, 21, tzinfo=timezone.utc
    )


def test_non_available_status_does_not_force_transaction_unresolved(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )

    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Faulted",
        observed_at="2026-09-22T10:20:00Z",
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.stopped_at is None


def test_buffered_meter_before_available_evidence_does_not_reactivate(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at="2026-09-22T10:20:00Z",
    )

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:10:00Z",
                "sampledValue": [{"value": "120"}],
            }
        ],
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, 20, tzinfo=timezone.utc
    )


def test_newer_meter_evidence_reactivates_unresolved_transaction(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    reconcile_connector_status(
        charger=recovery_charger,
        connector_number=1,
        status="Available",
        observed_at="2026-09-22T10:20:00Z",
    )

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:25:00Z",
                "sampledValue": [{"value": "125"}],
            }
        ],
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, 25, tzinfo=timezone.utc
    )


def test_offline_transaction_event_remains_unresolved_until_newer_live_event(
    recovery_charger,
) -> None:
    selected = record_v201_transaction_event(
        charger=recovery_charger,
        event_type="Started",
        transaction_id="remote-offline",
        id_token="card",
        evse_id=1,
        connector_id=1,
        timestamp="2026-09-22T10:00:00Z",
        live_evidence=False,
    )

    assert selected.recovery_state == OcppTransaction.RecoveryState.UNRESOLVED

    record_v201_transaction_event(
        charger=recovery_charger,
        event_type="Updated",
        transaction_id="remote-offline",
        id_token="",
        evse_id=1,
        connector_id=1,
        timestamp="2026-09-22T10:05:00Z",
        live_evidence=True,
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE


def test_clear_stale_charger_state_removes_live_sessions_without_completing_them(
    recovery_charger,
) -> None:
    active = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="active",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    unresolved = start_transaction(
        charger=recovery_charger,
        connector_id=2,
        id_tag="unresolved",
        account=None,
        meter_start=200,
        timestamp="2026-09-22T10:05:00Z",
    )
    unresolved.recovery_state = OcppTransaction.RecoveryState.UNRESOLVED
    unresolved.save(update_fields=("recovery_state",))
    cleared_at = datetime(2026, 9, 22, 11, tzinfo=timezone.utc)

    cleared_ids = clear_stale_charger_state(
        recovery_charger,
        reason="charger physically idle",
        cleared_at=cleared_at,
    )

    assert cleared_ids == (active.pk, unresolved.pk)
    for selected in (active, unresolved):
        selected.refresh_from_db()
        assert selected.recovery_state == OcppTransaction.RecoveryState.CLEARED
        assert selected.stopped_at is None
        assert selected.recovery_cleared_at == cleared_at
        assert selected.recovery_clear_reason == "charger physically idle"

    assert not recovery_charger.transactions.active().exists()
    assert not recovery_charger.transactions.unresolved().exists()
    assert recovery_charger.transactions.cleared().count() == 2


def test_buffered_meter_before_operator_clear_does_not_reactivate_session(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    clear_stale_charger_state(
        recovery_charger,
        cleared_at=datetime(2026, 9, 22, 11, tzinfo=timezone.utc),
    )

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T10:30:00Z",
                "sampledValue": [{"value": "125"}],
            }
        ],
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.CLEARED
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 10, 30, tzinfo=timezone.utc
    )


def test_fresh_meter_after_operator_clear_can_reactivate_session(
    recovery_charger,
) -> None:
    selected = start_transaction(
        charger=recovery_charger,
        connector_id=1,
        id_tag="card",
        account=None,
        meter_start=100,
        timestamp="2026-09-22T10:00:00Z",
    )
    clear_stale_charger_state(
        recovery_charger,
        cleared_at=datetime(2026, 9, 22, 11, tzinfo=timezone.utc),
    )

    record_meter_values(
        transaction_id=selected.pk,
        charger=recovery_charger,
        meter_values=[
            {
                "timestamp": "2026-09-22T11:05:00Z",
                "sampledValue": [{"value": "130"}],
            }
        ],
    )
    selected.refresh_from_db()

    assert selected.recovery_state == OcppTransaction.RecoveryState.ACTIVE
    assert selected.last_activity_at == datetime(
        2026, 9, 22, 11, 5, tzinfo=timezone.utc
    )
