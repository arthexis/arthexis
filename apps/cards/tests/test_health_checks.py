from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management.base import CommandError

from apps.cards import health_checks
from apps.core.management.commands import health as health_command


def test_health_registry_routes_rfid_to_cards_owner():
    definition = health_command.HEALTH_CHECKS["core.rfid"]

    assert definition.runner == "apps.cards.health_checks.run_check_rfid"
    assert definition.app_selector == "apps.cards"


def test_rfid_health_check_uses_cards_reader(monkeypatch):
    monkeypatch.setattr(
        health_checks,
        "validate_rfid_value",
        lambda value, kind=None: {"value": value, "kind": kind},
    )
    stdout = StringIO()

    health_checks.run_check_rfid(
        stdout=stdout,
        rfid_value="04 AB",
        rfid_kind="MIFARE",
    )

    assert json.loads(stdout.getvalue()) == {"value": "04 AB", "kind": "MIFARE"}


def test_rfid_health_check_requires_value():
    with pytest.raises(CommandError, match="requires --rfid-value"):
        health_checks.run_check_rfid(stdout=StringIO())
