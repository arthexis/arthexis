import asyncio
import json

from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.transaction_scenario import (
    SingleTransactionScenario,
    run_single_transaction_scenario,
)


def test_single_transaction_scenario_uses_existing_primitives(tmp_path):
    async def exercise():
        seen = []
        sleeps = []
        responses = {
            "status": {
                "ok": True,
                "evidence_dir": str(tmp_path),
                "clock": {"mode": "advancing", "charger_time": "2026-10-01T03:00:00Z"},
                "authorization_timeout": 15.0,
            },
            "authorize": {"ok": True, "request_id": "auth-1"},
            "wait-result": {
                "ok": True,
                "completed": True,
                "request_id": "auth-1",
                "authorization": "Accepted",
                "error": None,
            },
            "transaction-start": {
                "ok": True,
                "request_id": "start-1",
                "started": True,
                "authorization": "Accepted",
                "transaction_id": 41,
                "connector": 1,
                "meter_wh": 1000,
            },
            "transaction-stop": {
                "ok": True,
                "request_id": "stop-1",
                "stopped": True,
                "transaction_id": 41,
                "connector": 1,
                "meter_wh": 1120,
            },
        }
        meter_index = 0

        async def send(charger, request):
            nonlocal meter_index
            seen.append((charger, dict(request)))
            if request["action"] == "meter":
                meter_index += 1
                return {
                    "ok": True,
                    "request_id": f"meter-{meter_index}",
                    "meter_wh": 1000 + meter_index * 40,
                }
            return responses[request["action"]]

        async def sleep(seconds):
            sleeps.append(seconds)

        result = await run_single_transaction_scenario(
            "GW001",
            "SECRET-CARD",
            SingleTransactionScenario(
                duration_seconds=60,
                meter_interval_seconds=30,
                meter_start=1000,
                power_w=7200,
                current_a=30,
                voltage_v=240,
                stop_reason="Local",
            ),
            send=send,
            sleep=sleep,
        )

        assert [request["action"] for _, request in seen] == [
            "status",
            "authorize",
            "wait-result",
            "transaction-start",
            "meter",
            "meter",
            "meter",
            "transaction-stop",
        ]
        assert seen[2][1]["timeout"] == 15.0
        assert seen[3][1]["meter_start"] == 1000
        assert "connector_id" not in seen[3][1]
        assert seen[4][1] == {
            "action": "meter",
            "connector_id": 1,
            "power_w": 7200,
            "current_a": 30,
            "voltage_v": 240,
        }
        assert seen[-1][1]["connector_id"] == 1
        assert seen[-1][1]["reason"] == "Local"
        assert sleeps == [30, 30]
        assert result["connector"] == 1
        assert result["meter_samples"] == 3
        assert result["transaction_id"] == 41
        assert result["meter_wh"] == 1120

        snapshot = json.loads((tmp_path / f"transaction-scenario-{result['run_id']}.json").read_text())
        assert snapshot["scenario"]["duration_seconds"] == 60
        assert snapshot["scenario"]["power_w"] == 7200
        assert snapshot["clock"]["mode"] == "advancing"
        assert snapshot["id_tag_sha256"] == RequestJournal.id_tag_fingerprint("SECRET-CARD")
        assert "SECRET-CARD" not in json.dumps(snapshot)
        assert (tmp_path / f"transaction-scenario-{result['run_id']}.json").stat().st_mode & 0o777 == 0o600

    asyncio.run(exercise())


def test_battery_scenario_uses_assigned_connector_and_stops_at_unplug_soc(tmp_path):
    async def exercise():
        seen = []
        meter_index = 0

        async def send(charger, request):
            nonlocal meter_index
            seen.append(dict(request))
            action = request["action"]
            if action == "status":
                return {
                    "ok": True,
                    "evidence_dir": str(tmp_path),
                    "clock": {"mode": "host"},
                    "authorization_timeout": 10,
                }
            if action == "authorize":
                return {"ok": True, "request_id": "auth-1"}
            if action == "wait-result":
                return {"ok": True, "authorization": "Accepted", "error": None}
            if action == "transaction-start":
                return {
                    "ok": True,
                    "request_id": "start-1",
                    "started": True,
                    "authorization": "Accepted",
                    "connector": 2,
                    "transaction_id": 51,
                    "meter_wh": 500,
                }
            if action == "meter":
                meter_index += 1
                return {
                    "ok": True,
                    "request_id": f"meter-{meter_index}",
                    "meter_wh": int(request["energy_wh"]),
                }
            return {
                "ok": True,
                "request_id": "stop-1",
                "transaction_id": 51,
                "meter_wh": int(seen[-2]["energy_wh"]),
            }

        result = await run_single_transaction_scenario(
            "GW001",
            "CARD-BATTERY",
            SingleTransactionScenario(
                battery_kwh=0.1,
                start_soc=20,
                target_soc=90,
                unplug_soc=30,
                meter_interval_seconds=30,
                seed=3,
            ),
            send=send,
        )

        start = next(item for item in seen if item["action"] == "transaction-start")
        assert "connector_id" not in start
        meters = [item for item in seen if item["action"] == "meter"]
        assert meters
        assert all(item["connector_id"] == 2 for item in meters)
        assert all(item["power_w"] < 7200 for item in meters)
        assert all(238.8 <= item["voltage_v"] <= 241.2 for item in meters)
        stop = seen[-1]
        assert stop["connector_id"] == 2
        assert stop["reason"] == "EVDisconnected"
        assert result["connector"] == 2
        assert result["final_soc"] == 30
        assert result["delivered_wh"] == 10
        assert result["early_unplug"] is True

    asyncio.run(exercise())


def test_single_transaction_scenario_stops_after_denied_authorization(tmp_path):
    async def exercise():
        seen = []

        async def send(charger, request):
            seen.append(dict(request))
            if request["action"] == "status":
                return {
                    "ok": True,
                    "evidence_dir": str(tmp_path),
                    "clock": {"mode": "host"},
                    "authorization_timeout": 10.0,
                }
            if request["action"] == "authorize":
                return {"ok": True, "request_id": "auth-2"}
            return {
                "ok": True,
                "completed": True,
                "request_id": "auth-2",
                "authorization": "Blocked",
                "error": None,
            }

        result = await run_single_transaction_scenario(
            "GW001",
            "CARD-X",
            SingleTransactionScenario(duration_seconds=0),
            send=send,
        )

        assert result["started"] is False
        assert result["authorization"] == "Blocked"
        assert result["meter_samples"] == 0
        assert [request["action"] for request in seen] == [
            "status",
            "authorize",
            "wait-result",
        ]

    asyncio.run(exercise())
