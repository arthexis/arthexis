from datetime import datetime, timedelta, timezone

import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.models import MeterValue, OcppTransaction
from apps.ocpp.simulator import run_v16_historical_backlog
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


class StepClock:
    def __init__(self, step: float = 0.01) -> None:
        self.value = 0.0
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


def test_historical_backlog_load_is_deterministic_and_measured() -> None:
    cutover = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    target = charger("backlog-load")
    target.authority_cutover_at = cutover
    target.save(update_fields=("authority_cutover_at",))

    result = async_to_sync(run_v16_historical_backlog)(
        target,
        meter_values=5,
        start_at=cutover - timedelta(days=1),
        spacing=timedelta(seconds=2),
        clock=StepClock(),
    )

    assert result.requested_meter_values == 5
    assert result.attempted == 7
    assert result.succeeded == 7
    assert result.failed == 0
    assert result.elapsed_seconds > 0
    assert result.throughput_per_second > 0
    assert result.mean_latency_seconds > 0
    assert result.max_latency_seconds >= result.mean_latency_seconds

    transaction = OcppTransaction.objects.get(charger=target)
    assert transaction.historical is True
    assert transaction.stopped_at is not None
    assert MeterValue.objects.filter(transaction=transaction).count() == 5


def test_historical_backlog_load_rejects_nonhistorical_window() -> None:
    cutover = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    target = charger("backlog-load-window")
    target.authority_cutover_at = cutover
    target.save(update_fields=("authority_cutover_at",))

    with pytest.raises(ValueError, match="predate authority cutover"):
        async_to_sync(run_v16_historical_backlog)(
            target,
            meter_values=5,
            start_at=cutover - timedelta(seconds=2),
            spacing=timedelta(seconds=1),
        )


def test_historical_backlog_load_requires_authority_cutover() -> None:
    target = charger("backlog-load-no-cutover")

    with pytest.raises(ValueError, match="requires authority_cutover_at"):
        async_to_sync(run_v16_historical_backlog)(
            target,
            meter_values=1,
            start_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )



def test_historical_backlog_load_injects_live_probes() -> None:
    cutover = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
    target = charger("backlog-load-live")
    target.authority_cutover_at = cutover
    target.save(update_fields=("authority_cutover_at",))

    result = async_to_sync(run_v16_historical_backlog)(
        target,
        meter_values=6,
        start_at=cutover - timedelta(days=1),
        live_every=2,
        live_action="Heartbeat",
        clock=StepClock(),
    )

    assert result.live_probes == 3
    assert result.live_failures == 0
    assert result.mean_live_latency_seconds > 0
    assert result.max_live_latency_seconds >= result.mean_live_latency_seconds
