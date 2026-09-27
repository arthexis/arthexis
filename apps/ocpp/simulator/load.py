"""Deterministic synthetic backlog load scenarios for OCPP 1.6."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from time import perf_counter
from collections.abc import Callable

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.simulator.client import OcppSimulator

Clock = Callable[[], float]


@dataclass(frozen=True)
class BacklogLoadResult:
    """Baseline protocol measurements for one synthetic backlog drain."""

    requested_meter_values: int
    attempted: int
    succeeded: int
    failed: int
    elapsed_seconds: float
    throughput_per_second: float
    mean_latency_seconds: float
    max_latency_seconds: float


async def run_v16_historical_backlog(
    charger: Charger,
    *,
    meter_values: int,
    start_at: datetime,
    spacing: timedelta = timedelta(seconds=1),
    clock: Clock = perf_counter,
) -> BacklogLoadResult:
    """Drain one deterministic historical transaction through real OCPP handlers."""
    if meter_values < 1:
        raise ValueError("meter_values must be positive")
    if start_at.tzinfo is None:
        raise ValueError("start_at must be timezone-aware")
    if spacing <= timedelta(0):
        raise ValueError("spacing must be positive")
    if charger.authority_cutover_at is None:
        raise ValueError("historical backlog load requires authority_cutover_at")
    stop_at = start_at + spacing * (meter_values + 1)
    if stop_at >= charger.authority_cutover_at:
        raise ValueError("synthetic backlog timestamps must predate authority cutover")

    client = OcppSimulator(charger=charger, version=ProtocolVersion.OCPP_16)
    latencies: list[float] = []
    attempted = 0
    succeeded = 0
    failed = 0
    run_started = clock()

    async def call(action: str, payload: dict[str, object]):
        nonlocal attempted, succeeded, failed
        attempted += 1
        started = clock()
        try:
            response = await client.call(action, payload)
        except Exception:
            failed += 1
            raise
        finally:
            latencies.append(max(0.0, clock() - started))
        succeeded += 1
        return response

    started = await call(
        "StartTransaction",
        {
            "connectorId": 1,
            "idTag": "backlog-load",
            "meterStart": 0,
            "timestamp": _timestamp(start_at),
        },
    )
    transaction_id = started.payload["transactionId"]

    for index in range(meter_values):
        observed_at = start_at + spacing * (index + 1)
        await call(
            "MeterValues",
            {
                "transactionId": transaction_id,
                "meterValue": [
                    {
                        "timestamp": _timestamp(observed_at),
                        "sampledValue": [
                            {
                                "value": str(index + 1),
                                "measurand": "Energy.Active.Import.Register",
                                "unit": "Wh",
                            }
                        ],
                    }
                ],
            },
        )

    await call(
        "StopTransaction",
        {
            "transactionId": transaction_id,
            "meterStop": meter_values,
            "timestamp": _timestamp(stop_at),
        },
    )

    elapsed = max(0.0, clock() - run_started)
    return BacklogLoadResult(
        requested_meter_values=meter_values,
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        elapsed_seconds=elapsed,
        throughput_per_second=(succeeded / elapsed) if elapsed else 0.0,
        mean_latency_seconds=(sum(latencies) / len(latencies)) if latencies else 0.0,
        max_latency_seconds=max(latencies, default=0.0),
    )


def _timestamp(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
