import pytest
from django.test import override_settings

from apps.ocpp.models import Charger, OcppPolicy
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



@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_single_charger_readiness_reports_protocol_and_cards(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    selected = Charger.objects.create(
        identity="charger-one",
        protocol_mode=Charger.AuthorizationMode.OPEN,
        authorization_mode=Charger.AuthorizationMode.RESTRICTED,
    )

    payload = evaluate_ocpp_readiness(charger=selected.identity)

    assert payload == {
        "charger": "charger-one",
        "ready": True,
        "enabled": True,
        "posture": "partial",
        "protocol": "permissive",
        "cards": "strict",
    }


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_single_charger_dimension_and_mode_predicates(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    selected = Charger.objects.create(
        identity="charger-predicate",
        protocol_mode=Charger.AuthorizationMode.OPEN,
        authorization_mode=Charger.AuthorizationMode.RESTRICTED,
    )

    assert (
        evaluate_ocpp_readiness("protocol", charger=selected.identity)
        == "permissive"
    )
    assert evaluate_ocpp_readiness(
        "protocol",
        "permissive",
        charger=selected.identity,
    ) is True
    assert evaluate_ocpp_readiness(
        "cards",
        "permissive",
        charger=selected.identity,
    ) is False
    assert evaluate_ocpp_readiness(
        "cards",
        "strict",
        charger=selected.identity,
    ) is True


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_disabled_charger_is_not_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    selected = Charger.objects.create(
        identity="charger-disabled",
        active=False,
        protocol_mode=Charger.AuthorizationMode.OPEN,
        authorization_mode=Charger.AuthorizationMode.OPEN,
    )

    assert evaluate_ocpp_readiness(charger=selected.identity) is False
    assert evaluate_ocpp_readiness(
        "protocol",
        "permissive",
        charger=selected.identity,
    ) is False


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_single_charger_is_not_ready_when_ocpp_listener_is_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(readiness, "interface_addresses", lambda interface: set())
    selected = Charger.objects.create(
        identity="charger-no-listener",
        protocol_mode=Charger.AuthorizationMode.OPEN,
        authorization_mode=Charger.AuthorizationMode.OPEN,
    )

    assert evaluate_ocpp_readiness(charger=selected.identity) is False


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_single_charger_rejects_admission_dimension(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    selected = Charger.objects.create(identity="charger-admission")

    with pytest.raises(ValueError, match="dimension must be one of: protocol, cards"):
        evaluate_ocpp_readiness("admission", charger=selected.identity)


def test_single_charger_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="Unknown charger: missing"):
        evaluate_ocpp_readiness(charger="missing")
