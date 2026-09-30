import json

import pytest
from django.core.management import CommandError, call_command

from apps.ocpp.management.commands.ocpp_simulator import Command


def test_boot_is_canonical_start_operation(monkeypatch):
    seen = {}

    def fake_open(self, options):
        seen.update(options)

    monkeypatch.setattr(Command, "_open", fake_open)

    call_command(
        "ocpp_simulator",
        "boot",
        "ws://127.0.0.1:9000",
        "--charger",
        "GW001",
    )

    assert seen["action"] == "boot"
    assert seen["endpoint"] == "ws://127.0.0.1:9000"
    assert seen["charger"] == "GW001"
    assert seen["idle_timeout"] == 0.0


def test_followup_command_uses_active_charger_without_repeating_identity(
    monkeypatch, capsys
):
    async def fake_send_control(charger, request):
        assert charger == "GW001"
        assert request == {"action": "status"}
        return {
            "ok": True,
            "charger": charger,
            "connected": True,
            "boot": "Accepted",
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: {"charger": "GW001", "url": "ws://127.0.0.1:9000"},
    )
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.send_control",
        fake_send_control,
    )

    call_command("ocpp_simulator", "status")

    assert json.loads(capsys.readouterr().out)["charger"] == "GW001"


def test_followup_command_rejects_different_requested_charger(monkeypatch):
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator.active_session",
        lambda: {"charger": "GW001", "url": "ws://127.0.0.1:9000"},
    )

    with pytest.raises(CommandError, match="stop it before targeting"):
        call_command("ocpp_simulator", "status", "--charger", "GW002")
