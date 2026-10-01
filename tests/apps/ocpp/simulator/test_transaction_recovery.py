import asyncio
import json


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_transaction_operations_have_correlated_durable_results(
    tmp_path, peer_factory, worker_factory
):
    async def exercise():
        peer = peer_factory(transaction_id=77)
        current = worker_factory(peer)

        started = await current.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 100}
        )
        metered = await current.dispatch(
            {"action": "meter", "energy_wh": 125, "power_w": 7200}
        )
        stopped = await current.dispatch({"action": "transaction-stop"})

        assert started["request_id"]
        assert metered["request_id"]
        assert stopped["request_id"]
        assert len({started["request_id"], metered["request_id"], stopped["request_id"]}) == 3

        results = read_jsonl(tmp_path / "results.jsonl")
        by_id = {item["request_id"]: item for item in results}
        assert by_id[started["request_id"]]["action"] == "StartTransaction"
        assert by_id[metered["request_id"]]["action"] == "MeterValues"
        assert by_id[stopped["request_id"]]["action"] == "StopTransaction"
        assert all(item["error"] is None for item in by_id.values())

    asyncio.run(exercise())


def test_transaction_evidence_never_persists_raw_id_tag(
    tmp_path, peer_factory, worker_factory
):
    async def exercise():
        current = worker_factory(peer_factory(transaction_id=77))
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 100}
        )

    asyncio.run(exercise())

    assert "SECRET-CARD" not in (tmp_path / "events.jsonl").read_text()
    assert "SECRET-CARD" not in (tmp_path / "results.jsonl").read_text()


def test_worker_recovers_active_transaction_without_raw_id_tag(
    peer_factory, worker_factory
):
    async def exercise():
        first = worker_factory(peer_factory(transaction_id=77))
        await first.dispatch(
            {"action": "transaction-start", "id_tag": "SECRET-CARD", "meter_start": 400}
        )
        await first.dispatch(
            {"action": "meter", "energy_wh": 475, "power_w": 7000, "voltage_v": 240}
        )

        replacement_peer = peer_factory(transaction_id=77)
        recovered = worker_factory(replacement_peer)
        connector = recovered._connectors[1]
        status = recovered._transaction_status(connector)
        assert status["transaction_id"] == 77
        assert status["status"] == "Charging"
        assert status["meter_wh"] == 475
        assert status["power_w"] == 7000
        assert status["voltage_v"] == 240
        assert connector.transaction is not None
        assert connector.transaction.id_tag is None

        await recovered.connect_and_boot()
        assert replacement_peer.calls[-1][0] == "StatusNotification"
        assert replacement_peer.calls[-1][1]["status"] == "Charging"

        stopped = await recovered.dispatch({"action": "transaction-stop"})
        assert stopped["stopped"] is True
        stop_payload = replacement_peer.payloads("StopTransaction")[0]
        assert "idTag" not in stop_payload

    asyncio.run(exercise())


def test_completed_stop_recovers_as_idle(peer_factory, worker_factory):
    async def exercise():
        first = worker_factory(peer_factory(transaction_id=77))
        await first.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 10}
        )
        await first.dispatch({"action": "transaction-stop", "meter_stop": 20})

        recovered = worker_factory(peer_factory(transaction_id=77))
        status = recovered._transaction_status(recovered._connectors[1])
        assert status["transaction_id"] is None
        assert status["status"] == "Available"
        assert status["meter_wh"] == 20

    asyncio.run(exercise())
