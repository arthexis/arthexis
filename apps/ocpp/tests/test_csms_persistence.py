"""Tests for CSMS persistence helpers that write Charger rows."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.ocpp.consumers.csms.persistence import (
    persist_legacy_meter_values,
    update_availability_state_records,
    update_status_notification_records,
)
from apps.ocpp.models import Charger


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
def test_persist_legacy_meter_values_updates_only_targeted_charger(charger_rows):
    """Legacy meter payload persistence should only update the selected charger row."""

    connector_two = charger_rows["connector_two"]
    other_aggregate = charger_rows["other_aggregate"]
    payload = {"meterValue": [{"sampledValue": [{"value": "123.4"}]}]}

    persist_legacy_meter_values(charger_pk=connector_two.pk, payload=payload)

    connector_two.refresh_from_db()
    other_aggregate.refresh_from_db()

    assert connector_two.last_meter_values == payload
    assert other_aggregate.last_meter_values == {}
