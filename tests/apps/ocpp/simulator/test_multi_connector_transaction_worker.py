import asyncio

import pytest

from apps.ocpp.simulator.network import LiveSimulatorError


def test_start_without_connector_uses_lowest_available_connector(
    peer_factory, worker_factory
):
    async def exercise():
        peer = peer_factory(transaction_id=100, increment_ids=True)
        current = worker_factory(peer, connectors=2, charger="DUAL001")

        first = await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        second = await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        assert first["connector"] == 1
        assert second["connector"] == 2
        assert first["transaction_id"] == 100
        assert second["transaction_id"] == 101
        assert [payload["connectorId"] for payload in peer.payloads("StartTransaction")] == [1, 2]

        with pytest.raises(LiveSimulatorError, match="no available connector"):
            await current.dispatch({"action": "transaction-start", "id_tag": "C"})

    asyncio.run(exercise())


def test_concurrent_automatic_starts_reserve_distinct_connectors(
    peer_factory, worker_factory
):
    async def exercise():
        peer = peer_factory(transaction_id=100, increment_ids=True)
        current = worker_factory(peer, connectors=2, charger="DUAL001")

        first, second = await asyncio.gather(
            current.dispatch({"action": "transaction-start", "id_tag": "A"}),
            current.dispatch({"action": "transaction-start", "id_tag": "B"}),
        )

        assert {first["connector"], second["connector"]} == {1, 2}
        assert {first["transaction_id"], second["transaction_id"]} == {100, 101}
        assert {
            payload["connectorId"] for payload in peer.payloads("StartTransaction")
        } == {1, 2}

    asyncio.run(exercise())


def test_two_connectors_charge_independently_and_freed_connector_is_reused(
    peer_factory, worker_factory
):
    async def exercise():
        peer = peer_factory(transaction_id=100, increment_ids=True)
        current = worker_factory(peer, connectors=2, charger="DUAL001")
        first = await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        second = await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        await current.dispatch(
            {"action": "meter", "connector_id": 2, "energy_wh": 500, "power_w": 7000}
        )
        stopped = await current.dispatch(
            {"action": "transaction-stop", "connector_id": 1, "meter_stop": 250}
        )
        assert stopped["connector"] == 1

        status = await current.dispatch({"action": "status"})
        by_connector = {item["connector"]: item for item in status["connectors"]}
        assert by_connector[1]["status"] == "Available"
        assert by_connector[1]["transaction_id"] is None
        assert by_connector[2]["status"] == "Charging"
        assert by_connector[2]["transaction_id"] == second["transaction_id"]
        assert by_connector[2]["meter_wh"] == 500

        third = await current.dispatch({"action": "transaction-start", "id_tag": "C"})
        assert third["connector"] == 1
        assert third["transaction_id"] != first["transaction_id"]

    asyncio.run(exercise())


def test_meter_and_stop_require_connector_when_more_than_one_transaction_is_active(
    peer_factory, worker_factory
):
    async def exercise():
        current = worker_factory(
            peer_factory(transaction_id=100, increment_ids=True),
            connectors=2,
            charger="DUAL001",
        )
        await current.dispatch({"action": "transaction-start", "id_tag": "A"})
        await current.dispatch({"action": "transaction-start", "id_tag": "B"})

        with pytest.raises(LiveSimulatorError, match="multiple active transactions"):
            await current.dispatch({"action": "meter"})
        with pytest.raises(LiveSimulatorError, match="multiple active transactions"):
            await current.dispatch({"action": "transaction-stop"})

    asyncio.run(exercise())


def test_boot_and_reconnect_announce_each_connector(peer_factory, worker_factory):
    async def exercise():
        peer = peer_factory(transaction_id=100, increment_ids=True)
        current = worker_factory(peer, connectors=2, charger="DUAL001")
        await current.connect_and_boot()
        assert [payload["connectorId"] for payload in peer.payloads("StatusNotification")] == [1, 2]

        peer.calls.clear()
        await current.reconnect()
        assert [payload["connectorId"] for payload in peer.payloads("StatusNotification")] == [1, 2]

    asyncio.run(exercise())
