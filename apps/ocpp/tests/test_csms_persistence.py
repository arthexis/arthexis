"""Tests for CSMS persistence helpers that write Charger rows."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.ocpp.consumers.csms.persistence import (
    persist_legacy_meter_values,
    sync_charger_error_security_event,
    update_availability_state_records,
    update_status_notification_records,
)
from apps.ocpp.models import Charger
from apps.ops.models import SecurityAlertEvent

@pytest.fixture
def charger_rows(db):
    """Create one aggregate row and multiple connector rows for one charger_id."""

    aggregate = Charger.objects.create(
        charger_id="CP-100",
        connector_id=None,
        last_status="Available",
        availability_state="Operative",
    )
    connector_one = Charger.objects.create(
        charger_id="CP-100",
        connector_id=1,
        last_status="Preparing",
        availability_state="Operative",
    )
    connector_two = Charger.objects.create(
        charger_id="CP-100",
        connector_id=2,
        last_status="Charging",
        availability_state="Operative",
    )
    other_aggregate = Charger.objects.create(
        charger_id="CP-OTHER",
        connector_id=None,
        last_status="Faulted",
        availability_state="Inoperative",
    )
    return {
        "aggregate": aggregate,
        "connector_one": connector_one,
        "connector_two": connector_two,
        "other_aggregate": other_aggregate,
    }

@pytest.mark.django_db
def test_update_status_notification_records_updates_aggregate_when_connector_is_none(
    charger_rows,
):
    """A connector_value of None should update only the aggregate row."""

    aggregate = charger_rows["aggregate"]
    connector_one = charger_rows["connector_one"]

    update_status_notification_records(
        charger_id="CP-100",
        connector_value=None,
        primary_charger=aggregate,
        aggregate_charger=aggregate,
        update_kwargs={"last_status": "Unavailable", "last_error_code": "E100"},
    )

    aggregate.refresh_from_db()
    connector_one.refresh_from_db()

    assert aggregate.last_status == "Unavailable"
    assert aggregate.last_error_code == "E100"
    assert connector_one.last_status == "Preparing"

@pytest.mark.django_db
def test_update_status_notification_records_updates_connector_without_corrupting_aggregate(
    charger_rows,
):
    """A connector-specific update should not clobber the aggregate row."""

    aggregate = charger_rows["aggregate"]
    connector_one = charger_rows["connector_one"]

    update_status_notification_records(
        charger_id="CP-100",
        connector_value=1,
        primary_charger=aggregate,
        aggregate_charger=aggregate,
        update_kwargs={"last_status": "Faulted", "last_error_code": "E-CONN-1"},
    )

    aggregate.refresh_from_db()
    connector_one.refresh_from_db()

    assert connector_one.last_status == "Faulted"
    assert connector_one.last_error_code == "E-CONN-1"
    assert aggregate.last_status == "Available"
    assert aggregate.last_error_code == ""

@pytest.mark.django_db
def test_update_status_notification_records_falls_back_to_aggregate_when_connector_missing(
    charger_rows,
):
    """If a connector row is missing, updates fall back to the aggregate row."""

    aggregate = charger_rows["aggregate"]

    update_status_notification_records(
        charger_id="CP-100",
        connector_value=999,
        primary_charger=aggregate,
        aggregate_charger=aggregate,
        update_kwargs={"last_status": "Unavailable", "last_error_code": "E-MISSING"},
    )

    aggregate.refresh_from_db()

    assert aggregate.last_status == "Unavailable"
    assert aggregate.last_error_code == "E-MISSING"

@pytest.mark.django_db
def test_update_availability_state_records_updates_only_matching_connector_rows(charger_rows):
    """Connector-specific availability updates must only touch matching connectors."""

    connector_one = charger_rows["connector_one"]
    connector_two = charger_rows["connector_two"]
    aggregate = charger_rows["aggregate"]
    touched = update_availability_state_records(
        charger_id="CP-100",
        connector_value=1,
        state="Inoperative",
        timestamp=timezone.now(),
    )

    connector_one.refresh_from_db()
    connector_two.refresh_from_db()
    aggregate.refresh_from_db()

    assert [row.pk for row in touched] == [connector_one.pk]
    assert connector_one.availability_state == "Inoperative"
    assert connector_two.availability_state == "Operative"
    assert aggregate.availability_state == "Operative"

@pytest.mark.django_db
def test_update_availability_state_records_updates_only_aggregate_rows(charger_rows):
    """Aggregate availability updates should only touch rows with connector_id=None."""

    aggregate = charger_rows["aggregate"]
    connector_one = charger_rows["connector_one"]
    updated_at = timezone.now()

    touched = update_availability_state_records(
        charger_id="CP-100",
        connector_value=None,
        state="Inoperative",
        timestamp=updated_at,
    )

    aggregate.refresh_from_db()
    connector_one.refresh_from_db()

    assert [row.pk for row in touched] == [aggregate.pk]
    assert aggregate.availability_state == "Inoperative"
    assert aggregate.availability_state_updated_at == updated_at
    assert connector_one.availability_state == "Operative"

@pytest.mark.django_db
def test_update_availability_state_records_returns_empty_when_no_rows_match(charger_rows):
    """No matching rows should result in no writes and an empty touched set."""

    touched = update_availability_state_records(
        charger_id="UNKNOWN-CP",
        connector_value=None,
        state="Inoperative",
        timestamp=timezone.now(),
    )

    assert touched == []

@pytest.mark.django_db
def test_sync_charger_error_security_event_records_faulted_status(charger_rows):
    """Faulted charger statuses should create active ops security events."""

    timestamp = timezone.now()

    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=1,
        status="Faulted",
        error_code="ConnectorLockFailure",
        status_timestamp=timestamp,
    )

    event = SecurityAlertEvent.objects.get(key="ocpp-charger-CP-100-1-error")

    assert event.is_active is True
    assert event.occurrence_count == 1
    assert event.last_occurred_at == timestamp
    assert "Faulted" in event.message

@pytest.mark.django_db
def test_sync_charger_error_security_event_avoids_duplicate_same_timestamp(charger_rows):
    """Repeated payloads with same timestamp should not inflate occurrence counts."""

    timestamp = timezone.now()

    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=2,
        status="Faulted",
        error_code="E-CONN-2",
        status_timestamp=timestamp,
    )
    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=2,
        status="Faulted",
        error_code="E-CONN-2",
        status_timestamp=timestamp,
    )

    event = SecurityAlertEvent.objects.get(key="ocpp-charger-CP-100-2-error")
    assert event.occurrence_count == 1

@pytest.mark.django_db
def test_sync_charger_error_security_event_deactivates_when_status_recovers(charger_rows):
    """Recovered chargers should deactivate previously active OCPP security events."""

    timestamp = timezone.now()

    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=None,
        status="Faulted",
        error_code="InternalError",
        status_timestamp=timestamp,
    )
    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=None,
        status="Available",
        error_code="NoError",
        status_timestamp=timestamp + timedelta(minutes=1),
    )

    event = SecurityAlertEvent.objects.get(key="ocpp-charger-CP-100-aggregate-error")
    assert event.is_active is False

@pytest.mark.django_db
def test_sync_charger_error_security_event_ignores_stale_fault_after_recovery(charger_rows):
    """Older fault replays should not reactivate an event after a newer recovery."""

    faulted_at = timezone.now()
    recovered_at = faulted_at + timedelta(minutes=2)

    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=1,
        status="Faulted",
        error_code="ConnectorLockFailure",
        status_timestamp=faulted_at,
    )
    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=1,
        status="Available",
        error_code="NoError",
        status_timestamp=recovered_at,
    )
    sync_charger_error_security_event(
        charger_id="CP-100",
        connector_value=1,
        status="Faulted",
        error_code="ConnectorLockFailure",
        status_timestamp=faulted_at,
    )

    event = SecurityAlertEvent.objects.get(key="ocpp-charger-CP-100-1-error")
    assert event.is_active is False
    assert event.last_occurred_at == recovered_at

@pytest.mark.django_db
def test_sync_charger_error_security_event_supports_long_charger_id_key(charger_rows):
    """Long charger IDs should persist without exceeding key length limits."""

    long_charger_id = "C" * 100

    sync_charger_error_security_event(
        charger_id=long_charger_id,
        connector_value=None,
        status="Faulted",
        error_code="InternalError",
        status_timestamp=timezone.now(),
    )

    event = SecurityAlertEvent.objects.get(key=f"ocpp-charger-{long_charger_id}-aggregate-error")
    assert len(event.key) > 120

@pytest.mark.django_db
def test_sync_charger_error_security_event_hashes_very_long_key(charger_rows):
    """Very long charger IDs should be reduced to a safe deterministic key."""

    long_charger_id = "CHARGER-" + ("X" * 300)

    sync_charger_error_security_event(
        charger_id=long_charger_id,
        connector_value="connector-with-very-long-label-" + ("Y" * 120),
        status="Faulted",
        error_code="InternalError",
        status_timestamp=timezone.now(),
    )

    event = SecurityAlertEvent.objects.get()
    assert len(event.key) <= 255
    assert event.key.startswith("ocpp-charger-")
    assert event.key.endswith("-error")
