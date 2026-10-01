import asyncio
import json

import pytest

from apps.ocpp.simulator.requests import RequestJournal
from apps.ocpp.simulator.scenario_sequence import load_scenario, run_scenario_sequence


def test_sequence_runs_transaction_idle_disconnect_reconnect_and_status(monkeypatch, tmp_path):
    async def exercise():
        seen = []
        sleeps = []

        async def send(charger, request):
            seen.append((charger, dict(request)))
            action = request["action"]
            if action == "status":
                return {
                    "ok": True,
                    "evidence_dir": str(tmp_path),
                    "clock": {"mode": "advancing"},
                    "connected": True,
                    "status": "Available",
                    "transaction_id": None,
                    "meter_wh": 1200,
                    "reconnects": 1,
                }
            if action == "disconnect":
                return {"ok": True, "connected": False}
            if action == "reconnect":
                return {
                    "ok": True,
                    "connected": True,
                    "boot": "Accepted",
                    "reconnects": 1,
                }
            raise AssertionError(f"unexpected primitive: {action}")

        async def sleep(seconds):
            sleeps.append(seconds)

        async def fake_transaction(charger, id_tag, scenario, *, send, sleep):
            assert charger == "GW001"
            assert id_tag == "SECRET-CARD"
            assert scenario.duration_seconds == 30
            assert scenario.power_w == 7200
            return {
                "ok": True,
                "run_id": "child-1",
                "completed": True,
                "started": True,
                "authorization": "Accepted",
                "transaction_id": 41,
                "meter_wh": 1060,
                "meter_samples": 2,
            }

        monkeypatch.setattr(
            "apps.ocpp.simulator.scenario_sequence.run_single_transaction_scenario",
            fake_transaction,
        )

        payload = {
            "name": "network-interruption",
            "steps": [
                {
                    "action": "transaction",
                    "id_tag": "SECRET-CARD",
                    "duration_seconds": 30,
                    "power_w": 7200,
                },
                {"action": "idle", "seconds": 5},
                {"action": "disconnect"},
                {"action": "reconnect"},
                {"action": "status"},
            ],
        }
        result = await run_scenario_sequence(
            "GW001", payload, send=send, sleep=sleep
        )

        assert result["completed"] is True
        assert result["steps_completed"] == 5
        assert [step["action"] for step in result["steps"]] == [
            "transaction",
            "idle",
            "disconnect",
            "reconnect",
            "status",
        ]
        assert sleeps == [5]
        assert [request["action"] for _, request in seen] == [
            "status",
            "disconnect",
            "reconnect",
            "status",
        ]

        snapshot = json.loads((tmp_path / f"scenario-run-{result['run_id']}.json").read_text())
        snapshot_text = json.dumps(snapshot)
        assert "SECRET-CARD" not in snapshot_text
        transaction = snapshot["scenario"]["steps"][0]
        assert transaction["id_tag_sha256"] == RequestJournal.id_tag_fingerprint(
            "SECRET-CARD"
        )
        assert (tmp_path / f"scenario-run-{result['run_id']}.json").stat().st_mode & 0o777 == 0o600

        evidence = json.loads(
            (tmp_path / f"scenario-run-{result['run_id']}-result.json").read_text()
        )
        assert evidence["completed"] is True
        assert len(evidence["steps"]) == 5
        assert "SECRET-CARD" not in json.dumps(evidence)
        assert (tmp_path / f"scenario-run-{result['run_id']}-result.json").stat().st_mode & 0o777 == 0o600

    asyncio.run(exercise())


def test_sequence_failure_writes_partial_result_evidence(tmp_path):
    async def exercise():
        async def send(charger, request):
            if request["action"] == "status":
                return {
                    "ok": True,
                    "evidence_dir": str(tmp_path),
                    "clock": {"mode": "host"},
                }
            if request["action"] == "disconnect":
                raise RuntimeError("network fixture failed")
            raise AssertionError(request)

        payload = {
            "steps": [
                {"action": "idle", "seconds": 2},
                {"action": "disconnect"},
            ]
        }
        with pytest.raises(Exception, match="failed at step 2"):
            await run_scenario_sequence(
                "GW001", payload, send=send, sleep=lambda seconds: asyncio.sleep(0)
            )

        result_files = list(tmp_path.glob("scenario-run-*-result.json"))
        assert len(result_files) == 1
        evidence = json.loads(result_files[0].read_text())
        assert evidence["completed"] is False
        assert evidence["failed_step"] == 2
        assert evidence["steps"] == [{"index": 1, "action": "idle", "seconds": 2.0}]

    asyncio.run(exercise())


def test_load_scenario_validates_and_preserves_transaction_input(tmp_path):
    source = tmp_path / "scenario.json"
    source.write_text(
        json.dumps(
            {
                "name": "two-session-check",
                "steps": [
                    {
                        "action": "transaction",
                        "id_tag": "CARD-1",
                        "duration_seconds": 0,
                        "meter_interval_seconds": 10,
                    },
                    {"action": "idle", "seconds": 1},
                    {"action": "status"},
                ],
            }
        )
    )

    loaded = load_scenario(source)

    assert loaded["name"] == "two-session-check"
    assert loaded["steps"][0]["id_tag"] == "CARD-1"


def test_load_scenario_rejects_unknown_steps(tmp_path):
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps({"steps": [{"action": "teleport"}]}))

    with pytest.raises(ValueError, match="unsupported action"):
        load_scenario(source)
