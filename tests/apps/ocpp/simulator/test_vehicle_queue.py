import asyncio

from apps.ocpp.simulator import vehicle_queue


def test_vehicle_queue_assigns_connectors_without_scenario_pinning(monkeypatch):
    async def exercise():
        calls = []
        first_release = asyncio.Event()
        second_started = asyncio.Event()

        async def send(charger, request):
            assert request == {"action": "status"}
            return {"ok": True, "connector_count": 2, "connectors": []}

        async def fake_run(charger, id_tag, scenario, *, send):
            calls.append((id_tag, scenario.connector_id))
            if id_tag == "A":
                await second_started.wait()
            elif id_tag == "B":
                second_started.set()
                first_release.set()
            elif id_tag == "C":
                assert first_release.is_set()
            return {
                "run_id": f"run-{id_tag}",
                "connector": scenario.connector_id,
                "started": True,
                "completed": True,
                "authorization": "Accepted",
                "transaction_id": ord(id_tag),
                "meter_wh": 100,
                "delivered_wh": 100,
                "final_soc": 80,
                "early_unplug": False,
            }

        monkeypatch.setattr(vehicle_queue, "run_single_transaction_scenario", fake_run)
        result = await vehicle_queue.run_vehicle_queue(
            "DUAL001",
            [
                {"id_tag": "A", "battery_kwh": 1, "target_soc": 10},
                {"id_tag": "B", "battery_kwh": 1, "target_soc": 10},
                {"id_tag": "C", "battery_kwh": 1, "target_soc": 10},
            ],
            send=send,
        )

        assert calls[0:2] == [("A", 1), ("B", 2)]
        assert calls[2][0] == "C"
        assert calls[2][1] in {1, 2}
        assert result["connector_count"] == 2
        assert result["vehicles_completed"] == 3
        assert [item["index"] for item in result["vehicles"]] == [1, 2, 3]
        assert all(item["connector"] in {1, 2} for item in result["vehicles"])

    asyncio.run(exercise())


def test_vehicle_queue_rejects_explicit_connector():
    try:
        vehicle_queue.validate_vehicle_queue(
            [{"id_tag": "A", "connector_id": 2}]
        )
    except ValueError as exc:
        assert "must not pin connector_id" in str(exc)
    else:
        raise AssertionError("queue accepted an explicitly pinned connector")
