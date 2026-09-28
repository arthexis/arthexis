from decimal import Decimal

import pytest
from django.utils import timezone

from apps.energy.models import CustomerAccount
from apps.energy.reporting import (
    MeterContinuity,
    SessionCompleteness,
    SourceEvidence,
    project_charging_session,
)
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
    assert result.id_tag == "RFID-1"
    assert result.energy_kwh == Decimal("3.5000")
    assert result.completeness is SessionCompleteness.COMPLETE
    assert result.meter_continuity is MeterContinuity.CONTINUOUS
    assert result.source_protocol_transaction_id == "tx-44"
    assert result.source_evidence == (
        SourceEvidence(kind="ocpp_transaction", reference="tx-44"),
        SourceEvidence(kind="meter_value", reference="sample-a"),
        SourceEvidence(kind="meter_value", reference=str(fallback_sample.pk)),
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
    assert result.site_key is None
    assert result.vehicle_key is None
    assert result.id_tag is None
