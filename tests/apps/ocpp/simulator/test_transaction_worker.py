import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.ocpp.simulator.network import LiveSimulatorConfig, LiveSimulatorError
from apps.ocpp.simulator.transaction_worker import TransactionalLiveSimulatorWorker


class ManualClock:
    def __init__(self):
        self.current = datetime(2026, 10, 1, 2, 0, tzinfo=timezone.utc)

    def now(self):
        return self.current

    def isoformat(self):
        return self.current.isoformat().replace("+00:00", "Z")

    def advance(self, seconds):
        self.current += timedelta(seconds=seconds)


class FakeTransactionSimulator:
    def __init__(self):
        self.connected = False
        self.calls = []
        self.start_status = "Accepted"
        self.transaction_id = 41
        self.stop_error = None
        self.meter_error = None

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def boot(self):
        return SimpleNamespace(
            status="Accepted",
            current_time="2026-10-01T02:00:00Z",
            interval=60,
        )

    async def call(self, action, payload):
        self.calls.append((action, payload))
        if action == "StartTransaction":
            return {
                "transactionId": self.transaction_id,
                "idTagInfo": {"status": self.start_status},
            }
        if action == "MeterValues" and self.meter_error is not None:
            raise self.meter_error
        if action == "StopTransaction":
            if self.stop_error is not None:
                raise self.stop_error
            return {"idTagInfo": {"status": "Accepted"}}
        return {}


def worker(tmp_path, fake):
    return TransactionalLiveSimulatorWorker(
        LiveSimulatorConfig(
            url="ws://example.test",
            charger="GW001",
            allow_insecure_ws=True,
            evidence_dir=str(tmp_path),
            heartbeat=False,
        ),
        simulator_factory=lambda config: fake,
    )


def use_manual_clock(current):
    clock = ManualClock()
    current._clock = clock
    current._meter_anchor = clock.now()
    return clock


def test_boot_announces_available(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.connect_and_boot()

        assert fake.calls[0][0] == "StatusNotification"
        assert fake.calls[0][1]["status"] == "Available"
        assert (await current.dispatch({"action": "status"}))["status"] == "Available"

    asyncio.run(exercise())


def test_start_transaction_transitions_to_charging(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)

        result = await current.dispatch(
            {
                "action": "transaction-start",
                "id_tag": "CARD-1",
                "connector_id": 1,
                "meter_start": 1200,
            }
        )

        assert result["started"] is True
        assert result["transaction_id"] == 41
        assert result["status"] == "Charging"
        assert result["meter_wh"] == 1200
        assert [action for action, _ in fake.calls] == [
            "StatusNotification",
            "StartTransaction",
            "StatusNotification",
        ]
        assert fake.calls[0][1]["status"] == "Preparing"
        assert fake.calls[1][1]["meterStart"] == 1200
        assert fake.calls[2][1]["status"] == "Charging"

    asyncio.run(exercise())


def test_rejected_start_returns_to_available(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        fake.start_status = "Blocked"
        current = worker(tmp_path, fake)

        result = await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-X"}
        )

        assert result["started"] is False
        assert result["authorization"] == "Blocked"
        assert result["transaction_id"] is None
        assert result["status"] == "Available"
        assert fake.calls[-1][1]["status"] == "Available"

    asyncio.run(exercise())


def test_meter_values_include_explicit_electrical_state(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        clock = use_manual_clock(current)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 1000}
        )

        clock.advance(30)
        result = await current.dispatch(
            {
                "action": "meter",
                "energy_wh": 1250,
                "power_w": 7200,
                "current_a": 30,
                "voltage_v": 240,
            }
        )

        assert result["meter_wh"] == 1250
        assert result["power_w"] == 7200
        assert result["current_a"] == 30
        assert result["voltage_v"] == 240
        action, payload = fake.calls[-1]
        assert action == "MeterValues"
        assert payload["connectorId"] == 1
        assert payload["transactionId"] == 41
        assert payload["meterValue"][0]["timestamp"] == "2026-10-01T02:00:30Z"
        samples = {
            sample["measurand"]: sample
            for sample in payload["meterValue"][0]["sampledValue"]
        }
        assert samples["Energy.Active.Import.Register"]["value"] == "1250"
        assert samples["Power.Active.Import"]["value"] == "7200.0"
        assert samples["Current.Import"]["value"] == "30.0"
        assert samples["Voltage"]["value"] == "240.0"

    asyncio.run(exercise())


def test_meter_accumulates_energy_from_simulated_elapsed_time(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        clock = use_manual_clock(current)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 1000}
        )
        await current.dispatch({"action": "meter", "power_w": 7200})

        clock.advance(30)
        status = await current.dispatch({"action": "status"})
        assert status["meter_wh"] == 1060

        clock.advance(30)
        metered = await current.dispatch({"action": "meter"})
        assert metered["meter_wh"] == 1120
        assert fake.calls[-1][1]["meterValue"][0]["sampledValue"][0]["value"] == "1120"

    asyncio.run(exercise())


def test_meter_does_not_accumulate_when_simulated_clock_does_not_advance(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        use_manual_clock(current)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 500}
        )
        await current.dispatch({"action": "meter", "power_w": 11000})

        status = await current.dispatch({"action": "status"})
        assert status["meter_wh"] == 500

    asyncio.run(exercise())


def test_meter_failure_preserves_meter_state_for_retry(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        use_manual_clock(current)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 100}
        )
        fake.meter_error = LiveSimulatorError("meter transport failed")

        with pytest.raises(LiveSimulatorError, match="meter transport failed"):
            await current.dispatch(
                {"action": "meter", "energy_wh": 150, "power_w": 7000}
            )

        status = await current.dispatch({"action": "status"})
        assert status["meter_wh"] == 150
        assert status["power_w"] == 7000
        fake.meter_error = None
        retry = await current.dispatch({"action": "meter"})
        assert retry["meter_wh"] == 150

    asyncio.run(exercise())


def test_stop_transaction_uses_accumulated_meter_by_default(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        clock = use_manual_clock(current)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 1000}
        )
        await current.dispatch({"action": "meter", "power_w": 3600})
        clock.advance(60)

        result = await current.dispatch({"action": "transaction-stop"})

        assert result["meter_wh"] == 1060
        stop = next(payload for action, payload in fake.calls if action == "StopTransaction")
        assert stop["meterStop"] == 1060

    asyncio.run(exercise())


def test_stop_transaction_clears_state_only_after_csms_accepts_call(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 100}
        )

        result = await current.dispatch(
            {"action": "transaction-stop", "meter_stop": 250, "reason": "Local"}
        )

        assert result["stopped"] is True
        assert result["transaction_id"] == 41
        assert result["meter_wh"] == 250
        assert result["status"] == "Available"
        status = await current.dispatch({"action": "status"})
        assert status["transaction_id"] is None
        assert status["status"] == "Available"
        assert fake.calls[-3][1]["status"] == "Finishing"
        assert fake.calls[-2][0] == "StopTransaction"
        assert fake.calls[-2][1]["transactionId"] == 41
        assert fake.calls[-1][1]["status"] == "Available"

    asyncio.run(exercise())


def test_failed_stop_retains_active_transaction_and_charging_state(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch(
            {"action": "transaction-start", "id_tag": "CARD-1", "meter_start": 100}
        )
        fake.stop_error = LiveSimulatorError("transport failed")

        with pytest.raises(LiveSimulatorError, match="transport failed"):
            await current.dispatch(
                {"action": "transaction-stop", "meter_stop": 250}
            )

        status = await current.dispatch({"action": "status"})
        assert status["transaction_id"] == 41
        assert status["status"] == "Charging"
        assert status["meter_wh"] == 100
        assert fake.calls[-1][1]["status"] == "Charging"

    asyncio.run(exercise())


def test_second_transaction_is_rejected_while_one_is_active(tmp_path):
    async def exercise():
        fake = FakeTransactionSimulator()
        current = worker(tmp_path, fake)
        await current.dispatch({"action": "transaction-start", "id_tag": "CARD-1"})

        with pytest.raises(LiveSimulatorError, match="already has transaction"):
            await current.dispatch({"action": "transaction-start", "id_tag": "CARD-2"})

    asyncio.run(exercise())
