import pytest

from apps.ocpp.models import Charger, OcppPolicy
from apps.ocpp.services import readiness
from apps.ocpp.services.readiness import (
    evaluate_ocpp_readiness,
    query_ocpp_readiness,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def ready_listener(settings, monkeypatch) -> None:
    settings.OCPP_TRUSTED_CHARGER_INTERFACE = "eth0"
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"} if interface == "eth0" else set(),
    )


@pytest.fixture
def unavailable_listener(settings, monkeypatch) -> None:
    settings.OCPP_TRUSTED_CHARGER_INTERFACE = "eth0"
    monkeypatch.setattr(readiness, "interface_addresses", lambda interface: set())


def configured_charger(
    identity: str,
    *,
    protocol: str = Charger.AuthorizationMode.OPEN,
    cards: str = Charger.AuthorizationMode.OPEN,
    active: bool = True,
) -> Charger:
    return Charger.objects.create(
        identity=identity,
        protocol_mode=protocol,
        authorization_mode=cards,
        active=active,
    )


def test_default_instance_ocpp_posture_is_permissive(ready_listener) -> None:
    payload = query_ocpp_readiness()

    assert payload["ready"] is True
    assert payload["posture"] == "permissive"
    assert payload["admission"] == "permissive"
    assert payload["protocol"] == "permissive"
    assert payload["cards"] == "permissive"
    assert evaluate_ocpp_readiness() == "permissive"


def test_mixed_instance_policy_reports_partial(ready_listener) -> None:
    policy = OcppPolicy.load()
    policy.protocol_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save(update_fields=("protocol_mode", "updated_at"))

    assert evaluate_ocpp_readiness() == "partial"
    assert evaluate_ocpp_readiness("admission") == "permissive"
    assert evaluate_ocpp_readiness("protocol") == "strict"
    assert evaluate_ocpp_readiness("cards") == "permissive"


def test_dimension_mode_form_is_boolean_predicate(ready_listener) -> None:
    assert evaluate_ocpp_readiness("protocol", "permissive") is True
    assert evaluate_ocpp_readiness("protocol", "strict") is False


def test_not_ready_returns_false_even_when_policy_is_permissive(
    unavailable_listener,
) -> None:
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


def test_single_charger_readiness_reports_protocol_and_cards(ready_listener) -> None:
    selected = configured_charger(
        "charger-one",
        cards=Charger.AuthorizationMode.RESTRICTED,
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


def test_single_charger_dimension_and_mode_predicates(ready_listener) -> None:
    selected = configured_charger(
        "charger-predicate",
        cards=Charger.AuthorizationMode.RESTRICTED,
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


def test_disabled_charger_is_not_ready(ready_listener) -> None:
    selected = configured_charger("charger-disabled", active=False)

    assert evaluate_ocpp_readiness(charger=selected.identity) is False
    assert evaluate_ocpp_readiness(
        "protocol",
        "permissive",
        charger=selected.identity,
    ) is False


def test_single_charger_is_not_ready_when_ocpp_listener_is_unavailable(
    unavailable_listener,
) -> None:
    selected = configured_charger("charger-no-listener")

    assert evaluate_ocpp_readiness(charger=selected.identity) is False


def test_single_charger_rejects_admission_dimension(ready_listener) -> None:
    selected = configured_charger("charger-admission")

    with pytest.raises(ValueError, match="dimension must be one of: protocol, cards"):
        evaluate_ocpp_readiness("admission", charger=selected.identity)


def test_single_charger_rejects_unknown_identity() -> None:
    with pytest.raises(ValueError, match="Unknown charger: missing"):
        evaluate_ocpp_readiness(charger="missing")


def test_chargers_returns_ordered_breakdown_and_partial_aggregates(
    ready_listener,
) -> None:
    configured_charger(
        "charger-b",
        protocol=Charger.AuthorizationMode.RESTRICTED,
    )
    configured_charger("charger-a")
    configured_charger(
        "charger-c",
        protocol=Charger.AuthorizationMode.RESTRICTED,
        cards=Charger.AuthorizationMode.RESTRICTED,
        active=False,
    )

    payload = evaluate_ocpp_readiness(chargers=True)

    assert payload["ready"] is True
    assert payload["protocol"] == "partial"
    assert payload["cards"] == "partial"
    assert [item["charger"] for item in payload["chargers"]] == [
        "charger-a",
        "charger-b",
        "charger-c",
    ]
    assert payload["chargers"][0] == {
        "charger": "charger-a",
        "ready": True,
        "enabled": True,
        "posture": "permissive",
        "protocol": "permissive",
        "cards": "permissive",
    }
    assert payload["chargers"][1]["posture"] == "partial"
    assert payload["chargers"][2]["ready"] is False
    assert payload["chargers"][2]["enabled"] is False


def test_chargers_dimension_and_mode_use_fleet_aggregate(ready_listener) -> None:
    configured_charger(
        "charger-one",
        cards=Charger.AuthorizationMode.RESTRICTED,
    )
    configured_charger(
        "charger-two",
        cards=Charger.AuthorizationMode.RESTRICTED,
    )

    assert evaluate_ocpp_readiness("protocol", chargers=True) == "permissive"
    assert evaluate_ocpp_readiness("cards", chargers=True) == "strict"
    assert evaluate_ocpp_readiness(
        "protocol",
        "permissive",
        chargers=True,
    ) is True
    assert evaluate_ocpp_readiness(
        "cards",
        "permissive",
        chargers=True,
    ) is False


def test_chargers_returns_false_when_listener_is_unavailable(
    unavailable_listener,
) -> None:
    configured_charger("charger-one")

    assert evaluate_ocpp_readiness(chargers=True) is False


def test_chargers_rejects_admission_dimension() -> None:
    with pytest.raises(ValueError, match="dimension must be one of: protocol, cards"):
        evaluate_ocpp_readiness("admission", chargers=True)


def test_charger_and_chargers_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        evaluate_ocpp_readiness(charger="charger-one", chargers=True)
