"""Regression tests for the chargers management command output."""

from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.ocpp.models import Charger, Transaction


pytestmark = [pytest.mark.django_db, pytest.mark.regression]


def test_chargers_command_uses_kw_for_total_energy_header():
    """Regression: the summary table should label total energy in kW, not kWh."""

    charger = Charger.objects.create(charger_id="CMD-CHARGER-1")
    Transaction.objects.create(
        charger=charger,
        connector_id=1,
        meter_start=0,
        meter_stop=22000,
        start_time=timezone.now(),
    )

    stdout = StringIO()
    call_command("chargers", stdout=stdout)

    output = stdout.getvalue()
    assert "Total Energy (kW)" in output
    assert "Total Energy (kWh)" not in output
