import json

from django.core.management import call_command


def test_scenario_run_loads_json_and_uses_active_charger(monkeypatch, tmp_path, capsys):
    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(
            {
                "name": "smoke",
                "steps": [
                    {"action": "idle", "seconds": 1},
                    {"action": "status"},
                ],
            }
        )
    )
    seen = []
    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_scenario.SimulatorCommand._resolve_active_charger",
        lambda requested: "GW001",
    )

    async def fake_run(charger, scenario):
        seen.append((charger, scenario))
        return {
            "ok": True,
            "charger": charger,
            "run_id": "scenario-1",
            "completed": True,
            "steps_completed": 2,
        }

    monkeypatch.setattr(
        "apps.ocpp.management.commands.ocpp_simulator_scenario.run_scenario_sequence",
        fake_run,
    )

    call_command("ocpp_simulator_scenario", "run", str(source))

    assert seen == [
        (
            "GW001",
            {
                "name": "smoke",
                "steps": [
                    {"action": "idle", "seconds": 1},
                    {"action": "status"},
                ],
            },
        )
    ]
    assert json.loads(capsys.readouterr().out)["run_id"] == "scenario-1"
