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
                "meter_start": 1200,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["transaction_id"] == 41


def test_transaction_start_can_pin_connector(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

    call_command("ocpp_simulator_transaction", "start", "CARD-1", "--connector", "2")

    assert seen == [
        (
            "GW001",
            {"action": "transaction-start", "id_tag": "CARD-1", "connector_id": 2},
        )
    ]
    capsys.readouterr()


def test_transaction_meter_forwards_electrical_state(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

    call_command(
        "ocpp_simulator_transaction",
        "meter",
        "--connector",
        "2",
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
                "connector_id": 2,
                "energy_wh": 1500.0,
                "power_w": 7200.0,
                "current_a": 30.0,
                "voltage_v": 240.0,
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["meter_wh"] == 1500


def test_transaction_stop_forwards_meter_reason_and_connector(monkeypatch, capsys):
    seen = []
    _patch_active(monkeypatch, seen)

    call_command(
        "ocpp_simulator_transaction",
        "stop",
        "--connector",
        "2",
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
                "connector_id": 2,
                "meter_stop": 1500,
                "reason": "Local",
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["status"] == "Charging"


def test_transaction_run_builds_duration_scenario(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_run(charger, id_tag, scenario):
        seen.append((charger, id_tag, scenario))
        return {
            "ok": True,
            "charger": charger,
            "run_id": "run-1",
            "completed": True,
            "transaction_id": 41,
            "meter_samples": 5,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.run_single_transaction_scenario",
        fake_run,
    )

    call_command(
        "ocpp_simulator_transaction",
        "run",
        "CARD-1",
        "--duration",
        "120",
        "--meter-interval",
        "30",
        "--meter-start",
        "1000",
        "--power-w",
        "7200",
        "--current-a",
        "30",
        "--voltage-v",
        "240",
        "--reason",
        "Local",
        "--authorization-timeout",
        "12",
    )

    assert len(seen) == 1
    charger, id_tag, scenario = seen[0]
    assert charger == "GW001"
    assert id_tag == "CARD-1"
    assert scenario.duration_seconds == 120
    assert scenario.connector_id is None
    assert scenario.meter_interval_seconds == 30
    assert scenario.meter_start == 1000
    assert scenario.power_w == 7200
    assert scenario.current_a == 30
    assert scenario.voltage_v == 240
    assert scenario.stop_reason == "Local"
    assert scenario.authorization_timeout == 12
    assert json.loads(capsys.readouterr().out)["run_id"] == "run-1"


def test_transaction_run_builds_default_battery_scenario(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_run(charger, id_tag, scenario):
        seen.append((charger, id_tag, scenario))
        return {"ok": True, "charger": charger, "run_id": "battery-1"}

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_transaction.run_single_transaction_scenario",
        fake_run,
    )

    call_command(
        "ocpp_simulator_transaction",
        "run",
        "CARD-2",
        "--battery",
        "75",
        "--start-soc",
        "20",
        "--target-soc",
        "90",
        "--unplug-soc",
        "65",
        "--seed",
        "11",
    )

    _, _, scenario = seen[0]
    assert scenario.duration_seconds is None
    assert scenario.battery_driven is True
    assert scenario.battery_kwh == 75
    assert scenario.start_soc == 20
    assert scenario.target_soc == 90
    assert scenario.unplug_soc == 65
    assert scenario.seed == 11
    assert scenario.connector_id is None
    assert json.loads(capsys.readouterr().out)["run_id"] == "battery-1"
