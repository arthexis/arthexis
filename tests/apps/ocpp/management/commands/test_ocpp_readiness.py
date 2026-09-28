import pytest
from django.test import override_settings

from apps.ocpp.models import OcppPolicy
from apps.ocpp.services import readiness
from apps.ocpp.services.readiness import (
    evaluate_ocpp_readiness,
    query_ocpp_readiness,
)

pytestmark = pytest.mark.django_db


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_default_instance_ocpp_posture_is_permissive(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"} if interface == "eth0" else set(),
    )

    payload = query_ocpp_readiness()

    assert payload["ready"] is True
    assert payload["posture"] == "permissive"
    assert payload["admission"] == "permissive"
    assert payload["protocol"] == "permissive"
    assert payload["cards"] == "permissive"
    assert evaluate_ocpp_readiness() == "permissive"


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_mixed_instance_policy_reports_partial(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    policy = OcppPolicy.load()
    policy.protocol_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save(update_fields=("protocol_mode", "updated_at"))

    assert evaluate_ocpp_readiness() == "partial"
    assert evaluate_ocpp_readiness("admission") == "permissive"
    assert evaluate_ocpp_readiness("protocol") == "strict"
    assert evaluate_ocpp_readiness("cards") == "permissive"


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_dimension_mode_form_is_boolean_predicate(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )

    assert evaluate_ocpp_readiness("protocol", "permissive") is True
    assert evaluate_ocpp_readiness("protocol", "strict") is False


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_not_ready_returns_false_even_when_policy_is_permissive(monkeypatch) -> None:
    monkeypatch.setattr(readiness, "interface_addresses", lambda interface: set())

    assert evaluate_ocpp_readiness() is False
    assert evaluate_ocpp_readiness("admission") is False
    assert evaluate_ocpp_readiness("cards", "permissive") is False


@pytest.mark.parametrize(
    ("dimension", "mode", "message"),
    [
        ("unknown", None, "dimension must be one of"),
        ("protocol", "partial", "mode must be one of"),
        (None, "permissive", "mode requires a dimension"),
    ],
)
def test_invalid_ready_ocpp_selector_is_rejected(
    dimension,
    mode,
    message,
) -> None:
    with pytest.raises(ValueError, match=message):
        evaluate_ocpp_readiness(dimension, mode)
