import json

from django.core.management import call_command


def test_transaction_start_forwards_to_active_simulator(monkeypatch, capsys):
    seen = []

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        return {
            "ok": True,
            "charger": charger,
            "started": True,
            "transaction_id": 41,
            "status": "Charging",
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator_transaction",
        "start",
        "CARD-1",
        "--meter-start",
        "1200",
    )

    assert seen == [
        (
            "GW001",
            {
                "action": "transaction-start",
                "id_tag": "CARD-1",
                "connector_id": 1,
                "meter_start": 1200,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["transaction_id"] == 41


def test_transaction_stop_forwards_meter_and_reason(monkeypatch, capsys):
    seen = []

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        return {
            "ok": True,
            "charger": charger,
            "stopped": True,
            "transaction_id": 41,
            "status": "Available",
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.send_control",
        fake_send_control,
    )

    call_command(
        "ocpp_simulator_transaction",
        "stop",
        "--meter-stop",
        "1500",
        "--reason",
        "Local",
    )

    assert seen == [
        (
            "GW001",
            {
                "action": "transaction-stop",
                "meter_stop": 1500,
                "reason": "Local",
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "Available"
