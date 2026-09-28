from datetime import timedelta
from decimal import Decimal

import pytest

from django.utils import timezone

from apps.energy.models import CustomerAccount
from apps.energy.reporting import (
    EnergyProvenance,
    MeterContinuity,
    ReportingCondition,
    SessionCompleteness,
    SourceEvidence,
    project_charging_session,
    project_reporting_period,
    projected_sessions_for_period,
)
from apps.nodes.models import Node, NodeRole
from apps.ocpp.models import MeterValue
from tests.apps.ocpp.builders import charger, connector, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def charging_session():
    selected_charger = charger("CP001")
    selected_connector = connector(selected_charger, number=2)
    account = CustomerAccount.objects.create(key="customer-a", name="Customer A")
    started_at = timezone.now()
    selected = transaction(
        selected_charger,
        "tx-44",
        started_at=started_at,
        stopped_at=started_at,
        connector=selected_connector,
        account=account,
        id_tag="RFID-1",
        meter_start=Decimal("1000"),
        meter_stop=Decimal("4500"),
        energy_kwh=Decimal("3.5000"),
    )
    MeterValue.objects.create(
        transaction=selected,
        sampled_at=started_at,
        value=Decimal("1000"),
        source_fingerprint="sample-a",
    )
    fallback_sample = MeterValue.objects.create(
        transaction=selected,
        sampled_at=started_at,
        value=Decimal("4500"),
    )
    return selected, fallback_sample


def test_projection_is_charger_independent_and_auditable(charging_session):
    selected, fallback_sample = charging_session

    result = project_charging_session(selected)

    assert result.session_id == "CP001:tx-44"
    assert result.charger_identity == "CP001"
    assert result.connector_number == 2
    assert result.account_key == "customer-a"
    assert result.customer_key == "customer-a"
    assert result.site_key is None
    assert result.vehicle_key is None
    assert result.id_tag == "RFID-1"
    assert result.energy_kwh == Decimal("3.5000")
    assert result.energy_provenance is EnergyProvenance.RETAINED_NORMALIZED
    assert result.completeness is SessionCompleteness.COMPLETE
    assert result.meter_continuity is MeterContinuity.CONTINUOUS
    assert result.source_protocol_transaction_id == "tx-44"
    assert result.source_evidence == (
        SourceEvidence(kind="ocpp_transaction", reference="tx-44"),
        SourceEvidence(kind="meter_value", reference="sample-a"),
        SourceEvidence(kind="meter_value", reference=str(fallback_sample.pk)),
    )
    assert result.conditions == (
        ReportingCondition.MISSING_SITE,
        ReportingCondition.MISSING_VEHICLE,
    )


def test_open_transaction_projects_as_in_progress():
    selected_charger = charger("CP-OPEN")
    selected = transaction(
        selected_charger,
        "tx-open",
        started_at=timezone.now(),
        meter_start=Decimal("1000"),
    )

    result = project_charging_session(selected)

    assert result.completeness is SessionCompleteness.IN_PROGRESS
    assert result.meter_continuity is MeterContinuity.UNKNOWN
    assert result.energy_provenance is EnergyProvenance.UNRESOLVED


def test_stopped_session_without_resolved_energy_is_incomplete():
    selected_charger = charger("CP-INCOMPLETE")
    started_at = timezone.now()
    selected = transaction(
        selected_charger,
        "tx-incomplete",
        started_at=started_at,
        stopped_at=started_at,
        meter_start=Decimal("1000"),
        meter_stop=Decimal("4500"),
    )

    result = project_charging_session(selected)

    assert result.completeness is SessionCompleteness.INCOMPLETE
    assert ReportingCondition.UNRESOLVED_ENERGY in result.conditions


def test_counter_reset_is_explicit_instead_of_becoming_negative_energy():
    selected_charger = charger("CP-RESET")
    started_at = timezone.now()
    selected = transaction(
        selected_charger,
        "tx-reset",
        started_at=started_at,
        stopped_at=started_at,
        meter_start=Decimal("9000"),
        meter_stop=Decimal("1000"),
    )

    result = project_charging_session(selected)

    assert result.meter_continuity is MeterContinuity.DISCONTINUOUS
    assert result.energy_kwh is None
    assert result.completeness is SessionCompleteness.INCOMPLETE
    assert ReportingCondition.METER_DISCONTINUITY in result.conditions


def test_missing_optional_attribution_remains_explicitly_unresolved():
    selected_charger = charger("CP-UNATTRIBUTED")
    started_at = timezone.now()
    selected = transaction(
        selected_charger,
        "tx-unattributed",
        started_at=started_at,
        stopped_at=started_at,
        energy_kwh=Decimal("1.0000"),
    )

    result = project_charging_session(selected)

    assert result.connector_number is None
    assert result.account_key is None
    assert result.customer_key is None
    assert result.site_key is None
    assert result.vehicle_key is None
    assert result.id_tag is None
    assert ReportingCondition.MISSING_CUSTOMER in result.conditions
    assert ReportingCondition.MISSING_SITE in result.conditions
    assert ReportingCondition.MISSING_VEHICLE in result.conditions


def test_period_projection_is_stable_and_includes_live_and_historical_sessions():
    selected_charger = charger("CP-PERIOD")
    start = timezone.now()
    earlier = transaction(
        selected_charger,
        "tx-live",
        started_at=start,
        stopped_at=start,
        energy_kwh=Decimal("1.0000"),
    )
    later = transaction(
        selected_charger,
        "tx-history",
        started_at=start + timedelta(minutes=5),
        stopped_at=start + timedelta(minutes=6),
        historical=True,
        energy_kwh=Decimal("2.0000"),
    )
    transaction(
        selected_charger,
        "tx-outside",
        started_at=start + timedelta(hours=2),
        stopped_at=start + timedelta(hours=2),
        energy_kwh=Decimal("3.0000"),
    )

    projected = list(
        projected_sessions_for_period(
            type(earlier).objects.all(),
            started_at=start,
            before=start + timedelta(hours=1),
        )
    )

    assert [item.source_protocol_transaction_id for item in projected] == [
        "tx-live",
        "tx-history",
    ]
    assert [item.historical for item in projected] == [False, True]


def test_stopped_session_with_missing_meter_boundaries_surfaces_source_gaps():
    selected_charger = charger("CP-GAPS")
    started_at = timezone.now()
    selected = transaction(
        selected_charger,
        "tx-gaps",
        started_at=started_at,
        stopped_at=started_at,
        energy_kwh=Decimal("1.0000"),
    )

    result = project_charging_session(selected)

    assert ReportingCondition.MISSING_METER_START in result.conditions
    assert ReportingCondition.MISSING_METER_STOP in result.conditions



def test_reporting_period_envelope_is_versioned_transport_safe_and_authoritative():
    satellite = Node.objects.create(
        identifier="gw004",
        display_name="GW004",
        role=NodeRole.SATELLITE,
    )
    selected_charger = charger("CP-ENVELOPE", node=satellite)
    start = timezone.now()
    transaction(
        selected_charger,
        "tx-a",
        started_at=start,
        stopped_at=start,
        energy_kwh=Decimal("1.2500"),
    )
    transaction(
        selected_charger,
        "tx-b",
        started_at=start + timedelta(minutes=1),
        stopped_at=start + timedelta(minutes=2),
        energy_kwh=Decimal("2.7500"),
    )

    period = project_reporting_period(
        type(selected_charger.transactions.first()).objects.all(),
        started_at=start,
        before=start + timedelta(hours=1),
    )
    payload = period.as_dict()

    assert period.authority_nodes == ("gw004",)
    assert period.total_energy_kwh == Decimal("4.0000")
    assert payload["schema_version"] == 1
    assert payload["authority_nodes"] == ["gw004"]
    assert payload["total_energy_kwh"] == "4.0000"
    assert [item["source_protocol_transaction_id"] for item in payload["sessions"]] == [
        "tx-a",
        "tx-b",
    ]
    assert all(item["authority_node_key"] == "gw004" for item in payload["sessions"])
    assert all(item["authority_node_role"] == "satellite" for item in payload["sessions"])
    assert isinstance(payload["sessions"][0]["energy_kwh"], str)
    assert isinstance(payload["sessions"][0]["conditions"], list)
    assert isinstance(payload["sessions"][0]["source_evidence"], list)


def test_reporting_period_can_aggregate_multiple_authoritative_satellites():
    first_node = Node.objects.create(
        identifier="gw004",
        display_name="GW004",
        role=NodeRole.SATELLITE,
    )
    second_node = Node.objects.create(
        identifier="gw005",
        display_name="GW005",
        role=NodeRole.SATELLITE,
    )
    first = charger("CP-A", node=first_node)
    second = charger("CP-B", node=second_node)
    start = timezone.now()
    transaction(
        first,
        "tx-a",
        started_at=start,
        stopped_at=start,
        energy_kwh=Decimal("1.0000"),
    )
    transaction(
        second,
        "tx-b",
        started_at=start + timedelta(minutes=1),
        stopped_at=start + timedelta(minutes=1),
        energy_kwh=Decimal("2.0000"),
    )

    period = project_reporting_period(
        type(first.transactions.first()).objects.all(),
        started_at=start,
        before=start + timedelta(hours=1),
    )

    assert period.authority_nodes == ("gw004", "gw005")
    assert period.total_energy_kwh == Decimal("3.0000")
