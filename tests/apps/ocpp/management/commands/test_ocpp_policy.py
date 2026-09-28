from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cards.models import CardCredential
from apps.ocpp.models import Charger, OcppPolicy
from apps.ocpp.services.authorization import authorize_id_tag

pytestmark = pytest.mark.django_db


def test_policy_reports_permissive_instance_defaults() -> None:
    stdout = StringIO()

    call_command("ocpp_policy", stdout=stdout)

    assert stdout.getvalue().strip() == (
        "admission=permissive protocol=permissive cards=permissive"
    )


def test_policy_updates_instance_dimensions_with_public_vocabulary() -> None:
    stdout = StringIO()

    call_command(
        "ocpp_policy",
        admission="strict",
        protocol="strict",
        cards="permissive",
        stdout=stdout,
    )

    policy = OcppPolicy.load()
    assert policy.charger_admission_mode == OcppPolicy.AdmissionMode.RESTRICTED
    assert policy.protocol_mode == OcppPolicy.AdmissionMode.RESTRICTED
    assert policy.card_mode == OcppPolicy.AdmissionMode.OPEN
    assert stdout.getvalue().strip() == (
        "admission=strict protocol=strict cards=permissive"
    )


def test_policy_updates_selected_charger_protocol_and_cards() -> None:
    selected = Charger.objects.create(identity="policy-charger")
    stdout = StringIO()

    call_command(
        "ocpp_policy",
        charger=selected.identity,
        protocol="permissive",
        cards="permissive",
        stdout=stdout,
    )

    selected.refresh_from_db()
    assert selected.protocol_mode == Charger.AuthorizationMode.OPEN
    assert selected.authorization_mode == Charger.AuthorizationMode.OPEN
    assert stdout.getvalue().strip() == (
        "charger=policy-charger protocol=permissive cards=permissive"
    )


def test_admission_is_instance_only() -> None:
    selected = Charger.objects.create(identity="policy-admission")

    with pytest.raises(CommandError, match="instance-only"):
        call_command(
            "ocpp_policy",
            charger=selected.identity,
            admission="strict",
        )


def test_policy_rejects_unknown_charger() -> None:
    with pytest.raises(CommandError, match="Unknown charger: missing"):
        call_command(
            "ocpp_policy",
            charger="missing",
            protocol="strict",
        )



def test_policy_card_permissiveness_changes_live_authorization_behavior() -> None:
    selected = Charger.objects.create(identity="policy-card-live")

    # Operator-created chargers begin strict.
    rejected = authorize_id_tag(charger=selected, id_tag="guest-before")
    assert rejected.accepted is False
    assert not CardCredential.objects.filter(external_id="guest-before").exists()

    call_command(
        "ocpp_policy",
        charger=selected.identity,
        cards="permissive",
        stdout=StringIO(),
    )
    selected.refresh_from_db()

    learned = authorize_id_tag(charger=selected, id_tag="guest-after")
    assert learned.accepted is True
    assert learned.card is not None
    assert learned.card.auto_learned is True

    call_command(
        "ocpp_policy",
        charger=selected.identity,
        cards="strict",
        stdout=StringIO(),
    )
    selected.refresh_from_db()

    rejected_again = authorize_id_tag(charger=selected, id_tag="guest-new")
    assert rejected_again.accepted is False
    assert not CardCredential.objects.filter(external_id="guest-new").exists()


def test_strict_card_mode_accepts_explicitly_trusted_card() -> None:
    selected = Charger.objects.create(identity="policy-card-trusted")
    trusted = CardCredential.objects.create(
        external_id="trusted-card",
        ocpp_id_tag="trusted-card",
        auto_learned=False,
    )

    result = authorize_id_tag(charger=selected, id_tag=trusted.ocpp_id_tag)

    assert result.accepted is True
    assert result.card == trusted



def test_legacy_policy_keywords_remain_programmatically_compatible() -> None:
    selected = Charger.objects.create(identity="legacy-policy")

    call_command(
        "ocpp_policy",
        charger_admission="restricted",
        stdout=StringIO(),
    )
    assert OcppPolicy.load().charger_admission_mode == OcppPolicy.AdmissionMode.RESTRICTED

    call_command(
        "ocpp_policy",
        charger=selected.identity,
        rfid="open",
        stdout=StringIO(),
    )
    selected.refresh_from_db()
    assert selected.authorization_mode == Charger.AuthorizationMode.OPEN
