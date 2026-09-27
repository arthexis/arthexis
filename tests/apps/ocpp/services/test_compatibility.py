import pytest

from apps.ocpp.models import CompatibilityEvidence
from apps.ocpp.services.compatibility import bounded_value, record_compatibility_evidence
from tests.apps.ocpp.builders import charger


pytestmark = pytest.mark.django_db


def test_compatibility_evidence_is_bounded_and_linked_to_charger() -> None:
    selected = charger("quirky")
    evidence = record_compatibility_evidence(
        kind="malformed_frame",
        charger=selected,
        protocol="ocpp1.6",
        details={"payload": ["x" * 1000] * 30},
    )

    assert evidence.charger == selected
    assert evidence.charger_identity == "quirky"
    assert len(evidence.details["payload"]) == 17
    assert len(evidence.details["payload"][0]) == 512
    assert evidence.details["payload"][-1] == "<truncated>"


def test_bounded_value_limits_nested_mapping_width() -> None:
    result = bounded_value({f"k{i}": i for i in range(40)})

    assert len(result) == 33
    assert result["<truncated>"] is True
    assert CompatibilityEvidence.objects.count() == 0
