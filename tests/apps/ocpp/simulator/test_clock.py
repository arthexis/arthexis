from datetime import datetime, timezone

from apps.ocpp.simulator.clock import ChargerClock


UTC = timezone.utc


def test_host_clock_uses_host_wall_time():
    clock = ChargerClock(wall_time=lambda: 1_700_000_000.0, monotonic=lambda: 10.0)

    assert clock.now() == datetime.fromtimestamp(1_700_000_000.0, tz=UTC)


def test_offset_clock_can_be_behind_host_time():
    clock = ChargerClock(
        mode="offset",
        offset_seconds=-300,
        wall_time=lambda: 1_700_000_000.0,
        monotonic=lambda: 10.0,
    )

    assert clock.now() == datetime.fromtimestamp(1_699_999_700.0, tz=UTC)


def test_frozen_clock_never_advances():
    monotonic_values = iter((10.0, 50.0, 100.0))
    clock = ChargerClock(
        mode="frozen",
        start_time="2026-09-30T12:00:00Z",
        monotonic=lambda: next(monotonic_values),
    )

    assert clock.isoformat() == "2026-09-30T12:00:00Z"
    assert clock.isoformat() == "2026-09-30T12:00:00Z"


def test_advancing_clock_uses_monotonic_elapsed_time():
    values = iter((100.0, 130.0, 175.5))
    clock = ChargerClock(
        mode="advancing",
        start_time="2026-09-30T12:00:00Z",
        monotonic=lambda: next(values),
    )

    assert clock.isoformat() == "2026-09-30T12:00:30Z"
    assert clock.isoformat() == "2026-09-30T12:01:15.500000Z"


def test_naive_start_time_is_interpreted_as_utc():
    clock = ChargerClock(
        mode="fixed",
        start_time="2026-09-30T12:00:00",
        monotonic=lambda: 0.0,
    )

    assert clock.isoformat() == "2026-09-30T12:00:00Z"
