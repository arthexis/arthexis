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
            },
            "transaction-stop": {
                "ok": True,
                "request_id": "stop-1",
                "stopped": True,
                "transaction_id": 41,
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
        assert seen[4][1] == {
            "action": "meter",
            "power_w": 7200,
            "current_a": 30,
            "voltage_v": 240,
        }
        assert seen[-1][1]["reason"] == "Local"
        assert sleeps == [30, 30]
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
