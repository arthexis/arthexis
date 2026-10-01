import json

from django.core.management import call_command


def _patch_active(monkeypatch, seen):
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_send_control(charger, request):
        seen.append((charger, request))
        return {
            "ok": True,
            "charger": charger,
            "status": "Charging",
            "transaction_id": 41,
            "meter_wh": 1500,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.send_control",
        fake_send_control,
    )


def test_transaction_start_forwards_to_active_simulator(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

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


def test_transaction_meter_forwards_electrical_state(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

    call_command(
        "ocpp_simulator_transaction",
        "meter",
        "--energy-wh",
        "1500",
        "--power-w",
        "7200",
        "--current-a",
        "30",
        "--voltage-v",
        "240",
    )

    assert seen == [
        (
            "GW001",
            {
                "action": "meter",
                "energy_wh": 1500.0,
                "power_w": 7200.0,
                "current_a": 30.0,
                "voltage_v": 240.0,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["meter_wh"] == 1500


def test_transaction_stop_forwards_meter_and_reason(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

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
    assert json.loads(capsys.readouterr().out)["status"] == "Charging"
