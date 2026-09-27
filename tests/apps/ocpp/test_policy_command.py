from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.models import Charger, OcppPolicy
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_policy_defaults_to_open_charger_admission() -> None:
    output = StringIO()

    call_command("ocpp_policy", stdout=output)

    assert output.getvalue().strip() == "charger_admission=open"
    assert OcppPolicy.load().charger_admission_mode == OcppPolicy.AdmissionMode.OPEN


def test_policy_command_can_restrict_instance_charger_admission() -> None:
    output = StringIO()

    call_command("ocpp_policy", charger_admission="restricted", stdout=output)

    assert OcppPolicy.load().charger_admission_mode == OcppPolicy.AdmissionMode.RESTRICTED
    assert "charger_admission=restricted" in output.getvalue()


def test_policy_command_can_tighten_one_chargers_rfid_policy() -> None:
    selected = charger("charger-1")
    assert selected.authorization_mode == Charger.AuthorizationMode.OPEN
    output = StringIO()

    call_command(
        "ocpp_policy",
        charger="charger-1",
        rfid="restricted",
        stdout=output,
    )

    selected.refresh_from_db()
    assert selected.authorization_mode == Charger.AuthorizationMode.RESTRICTED
    assert "charger=charger-1 rfid=restricted" in output.getvalue()


def test_rfid_policy_requires_a_charger_selection() -> None:
    with pytest.raises(CommandError, match="--rfid requires --charger"):
        call_command("ocpp_policy", rfid="restricted")
