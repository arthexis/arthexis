from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.models import Charger, OcppPolicy

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
