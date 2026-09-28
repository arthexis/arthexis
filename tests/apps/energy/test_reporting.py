from decimal import Decimal
from types import SimpleNamespace

from apps.energy.reporting import (
    MeterContinuity,
    SessionCompleteness,
    SourceEvidence,
    project_charging_session,
)


class MeterValues:
    def __init__(self, *values):
        self.values = values

    def all(self):
        return self.values


def transaction(**overrides):
    values = {
        "pk": 17,
        "charger": SimpleNamespace(identity="CP001"),
        "connector": SimpleNamespace(number=2),
        "account": SimpleNamespace(key="customer-a"),
        "id_tag": "RFID-1",
        "remote_id": "tx-44",
        "started_at": "2026-09-28T01:00:00Z",
        "stopped_at": "2026-09-28T02:00:00Z",
        "meter_start": Decimal("1000"),
        "meter_stop": Decimal("4500"),
        "energy_kwh": Decimal("3.5000"),
        "historical": False,
        "meter_values": MeterValues(
            SimpleNamespace(pk=1, source_fingerprint="sample-a"),
            SimpleNamespace(pk=2, source_fingerprint=""),
        ),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_projection_is_charger_independent_and_auditable():
    result = project_charging_session(transaction())

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
        SourceEvidence(kind="meter_value", reference="2"),
    )


def test_open_transaction_projects_as_in_progress():
    result = project_charging_session(
        transaction(stopped_at=None, meter_stop=None, energy_kwh=None)
    )

    assert result.completeness is SessionCompleteness.IN_PROGRESS
    assert result.meter_continuity is MeterContinuity.UNKNOWN


def test_stopped_session_without_resolved_energy_is_incomplete():
    result = project_charging_session(transaction(energy_kwh=None))

    assert result.completeness is SessionCompleteness.INCOMPLETE


def test_counter_reset_is_explicit_instead_of_becoming_negative_energy():
    result = project_charging_session(
        transaction(
            meter_start=Decimal("9000"),
            meter_stop=Decimal("1000"),
            energy_kwh=None,
        )
    )

    assert result.meter_continuity is MeterContinuity.DISCONTINUOUS
    assert result.energy_kwh is None
    assert result.completeness is SessionCompleteness.INCOMPLETE


def test_missing_optional_attribution_remains_explicitly_unresolved():
    result = project_charging_session(
        transaction(connector=None, account=None, id_tag="")
    )

    assert result.connector_number is None
    assert result.account_key is None
    assert result.site_key is None
    assert result.vehicle_key is None
    assert result.id_tag is None
