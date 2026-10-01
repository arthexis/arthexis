import math

from apps.ocpp.simulator.battery import BatteryDeliveryModel, BatterySession


def test_default_battery_session_is_sixty_kwh_zero_to_full():
    session = BatterySession()
    assert session.battery_kwh == 60
    assert session.start_soc == 0
    assert session.target_soc == 100
    assert session.requested_wh == 60000


def test_delivery_is_below_nominal_reproducible_and_clipped_exactly():
    session = BatterySession(
        battery_kwh=1,
        start_soc=20,
        target_soc=30,
        max_power_w=7200,
        meter_interval_seconds=30,
        seed=17,
    )
    first = BatteryDeliveryModel(session)
    second = BatteryDeliveryModel(session)
    trace_a = []
    trace_b = []
    while not first.completed:
        trace_a.append(first.next_chunk())
    while not second.completed:
        trace_b.append(second.next_chunk())

    assert [(x.delivered_wh, x.power_w) for x in trace_a] == [
        (x.delivered_wh, x.power_w) for x in trace_b
    ]
    assert all(chunk.power_w < session.max_power_w for chunk in trace_a)
    assert math.isclose(first.delivered_wh, session.requested_wh)
    assert math.isclose(trace_a[-1].state_of_charge, 30.0)
    assert trace_a[-1].delivered_wh <= (
        trace_a[-1].power_w * session.meter_interval_seconds / 3600
    )


def test_power_tapers_near_full_while_line_voltage_stays_nearly_flat():
    model = BatteryDeliveryModel(
        BatterySession(
            battery_kwh=1,
            start_soc=79,
            target_soc=100,
            max_power_w=7200,
            meter_interval_seconds=30,
            taper_start_soc=80,
            seed=5,
        )
    )
    chunks = []
    while not model.completed:
        chunks.append(model.next_chunk())

    early = next(chunk for chunk in chunks if chunk.state_of_charge >= 80)
    late = next(chunk for chunk in chunks if chunk.state_of_charge >= 95)
    assert late.power_w < early.power_w
    assert late.current_a < early.current_a
    assert all(238.8 <= chunk.voltage_v <= 241.2 for chunk in chunks)


def test_early_unplug_limits_requested_energy():
    session = BatterySession(
        battery_kwh=60,
        start_soc=20,
        target_soc=90,
        unplug_soc=65,
    )
    assert session.requested_wh == 27000
    assert session.effective_stop_soc == 65
