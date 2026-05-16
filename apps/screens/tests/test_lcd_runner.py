"""Tests for the LCD runner coordinator helpers."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from apps.screens.lcd_screen import runner


class DummyFuture:
    """Provide a minimal future implementation for shutdown tests."""

    def done(self) -> bool:
        """Report the future as already complete."""

        return True

    def result(self):
        """Return no prefetched cycle."""

        return None


class StaticFuture:
    """Return a fixed prefetched cycle without spawning threads."""

    def __init__(self, value) -> None:
        self.value = value

    def done(self) -> bool:
        return True

    def result(self):
        return self.value


class DummyExecutor:
    """Capture shutdown requests without spawning threads."""

    def __init__(self) -> None:
        """Initialize executor bookkeeping."""

        self.shutdown_calls: list[tuple[bool, bool]] = []

    def shutdown(self, wait: bool, cancel_futures: bool) -> None:
        """Record executor shutdown arguments."""

        self.shutdown_calls.append((wait, cancel_futures))


class FakeLockFile:
    """Emulate an event lock file that can be unlinked."""

    def __init__(self) -> None:
        """Initialize unlink state."""

        self.unlinked = False

    def unlink(self) -> None:
        """Record lock removal."""

        self.unlinked = True


class FakeScheduler:
    """Capture scheduler activity without sleeping."""

    def __init__(self) -> None:
        """Initialize scheduler call history."""

        self.actions: list[tuple[str, float | None]] = []

    def sleep_until_ready(self) -> None:
        """Record that rendering waited for the next slot."""

        self.actions.append(("sleep", None))

    def advance(self, interval: float) -> None:
        """Record the next rendering interval."""

        self.actions.append(("advance", interval))


class FakeWriter:
    """Provide a frame writer with an attachable LCD reference."""

    def __init__(self, lcd) -> None:
        """Store the initial LCD reference."""

        self.lcd = lcd


def test_disable_lcd_resets_mutable_runner_state(monkeypatch):
    """Disabling the LCD should clear active state and switch to fallback output."""

    blanked: list[object] = []
    monkeypatch.setattr(runner, "_blank_display", lambda lcd: blanked.append(lcd))

    coordinator = runner.LCDRunner()
    initial_lcd = object()
    coordinator.lcd = initial_lcd
    coordinator.rotation.display_state = object()
    coordinator.rotation.next_display_state = object()
    coordinator.event.payload = runner.locks.EventPayload(["alert"], 0)
    coordinator.event.deadline = datetime.now(timezone.utc)
    coordinator.event.lock_file = Path("event.lck")
    coordinator.event.line_index = 2
    coordinator.event.line_deadline = 12.5
    previous_history = coordinator.history_recorder

    coordinator.disable_lcd("test disable")

    assert coordinator.lcd_disabled is True
    assert blanked == [initial_lcd]
    assert coordinator.lcd is None
    assert coordinator.rotation.display_state is None
    assert coordinator.rotation.next_display_state is None
    assert coordinator.event.payload is None
    assert coordinator.event.deadline is None
    assert coordinator.event.lock_file is None
    assert coordinator.event.line_index == 0
    assert coordinator.event.line_deadline == 0.0
    assert coordinator.frame_writer.lcd is None
    assert coordinator.frame_writer.history_recorder is previous_history


def test_disable_lcd_logs_info_for_expected_missing_hardware(caplog):
    coordinator = runner.LCDRunner()

    with caplog.at_level(logging.INFO):
        coordinator.disable_lcd(
            "LCD unavailable during startup",
            runner.LCDUnavailableError(
                "I2C bus device for channel 1 is unavailable ([Errno 2] No such file or directory)"
            ),
        )

    assert "Disabling LCD feature" in caplog.text
    assert "WARNING" not in caplog.text


def test_disable_lcd_logs_warning_for_unexpected_failures(caplog):
    coordinator = runner.LCDRunner()

    with caplog.at_level(logging.WARNING):
        coordinator.disable_lcd("LCD startup failed", RuntimeError("permission denied"))

    assert "Disabling LCD feature" in caplog.text


def test_handle_external_event_interrupt_loads_and_renders(monkeypatch):
    """An interrupt should reload event state and render the event frame immediately."""

    coordinator = runner.LCDRunner()
    coordinator.scroll_scheduler = FakeScheduler()
    coordinator.cycle_prefetch_executor = DummyExecutor()
    coordinator.frame_writer = FakeWriter(object())
    coordinator.lcd = object()

    payload = runner.locks.EventPayload(["one", "two", "three"], 25)
    event_deadline = datetime(2026, 3, 20, tzinfo=timezone.utc) + timedelta(minutes=5)
    lock_file = FakeLockFile()
    advanced_states: list[object] = []

    monkeypatch.setattr(runner, "_event_interrupt_requested", lambda: True)
    reset_calls: list[str] = []
    monkeypatch.setattr(
        runner,
        "_reset_event_interrupt_flag",
        lambda: reset_calls.append("reset"),
    )
    monkeypatch.setattr(
        runner,
        "_load_next_event",
        lambda now_dt: (payload, event_deadline, lock_file),
    )
    monkeypatch.setattr(
        runner,
        "_prepare_display_state",
        lambda line1, line2, scroll_ms: SimpleNamespace(
            line1=line1,
            line2=line2,
            scroll_ms=scroll_ms,
            scroll_sec=0.25,
            steps1=2,
            steps2=2,
        ),
    )

    def fake_advance_display(state, frame_writer, **kwargs):
        advanced_states.append(state)
        return state, True, False

    monkeypatch.setattr(runner, "_advance_display", fake_advance_display)

    handled = coordinator.handle_external_event(
        now=50.0,
        now_dt=datetime(2026, 3, 20, tzinfo=timezone.utc),
    )

    assert handled is True
    assert reset_calls == ["reset"]
    assert coordinator.event.payload == payload
    assert coordinator.event.deadline == event_deadline
    assert coordinator.event.lock_file is lock_file
    assert coordinator.event.display_state.line1 == "one"
    assert coordinator.event.display_state.line2 == "two"
    assert coordinator.event.line_deadline == 60.0
    assert advanced_states and advanced_states[0].line1 == "one"
    assert coordinator.scroll_scheduler.actions == [("sleep", None), ("advance", 0.25)]


def test_event_expiry_clears_rotation_state_when_no_followup(monkeypatch):
    """Expiring the last event should restore rotation scheduling state."""

    coordinator = runner.LCDRunner()
    coordinator.cycle_prefetch_executor = DummyExecutor()
    coordinator.event.payload = runner.locks.EventPayload(["old"], 0)
    coordinator.event.deadline = datetime(2026, 3, 20, tzinfo=timezone.utc)
    coordinator.event.lock_file = FakeLockFile()
    coordinator.rotation.order = ("high", "low", "clock")
    coordinator.rotation.index = 0
    coordinator.rotation.display_state = object()
    coordinator.rotation.next_display_state = object()
    coordinator.rotation.deadline = 25.0

    monkeypatch.setattr(runner, "_load_next_event", lambda now_dt: (None, None, None))

    coordinator.expire_current_event(datetime(2026, 3, 20, tzinfo=timezone.utc))

    assert coordinator.event.payload is None
    assert coordinator.rotation.index == 1
    assert coordinator.rotation.display_state is None
    assert coordinator.rotation.next_display_state is None
    assert coordinator.rotation.deadline == 0.0


def test_render_rotation_frame_handles_hot_unplug_without_attribute_error(monkeypatch):
    """Rotation rendering should tolerate the LCD being disabled during health checks."""

    coordinator = runner.LCDRunner()
    coordinator.scroll_scheduler = FakeScheduler()
    active_lcd = object()
    coordinator.lcd = active_lcd
    coordinator.frame_writer = FakeWriter(None)
    coordinator.rotation.order = ("clock",)
    coordinator.rotation.index = 0
    coordinator.rotation.display_state = SimpleNamespace(scroll_sec=0.5)

    blanked: list[object] = []
    monkeypatch.setattr(runner, "_blank_display", lambda lcd: blanked.append(lcd))
    monkeypatch.setattr(
        runner,
        "_advance_display",
        lambda state, frame_writer, **kwargs: (state, False, False),
    )

    coordinator.render_rotation_frame()

    assert coordinator.lcd_disabled is True
    assert blanked == [active_lcd]
    assert coordinator.rotation.display_state is None
    assert coordinator.scroll_scheduler.actions == [
        ("sleep", None),
        ("advance", runner.DEFAULT_FALLBACK_SCROLL_SEC),
    ]


def test_render_event_frame_raises_stop_iteration_on_shutdown(monkeypatch):
    """Event rendering should stop the loop immediately when shutdown is requested."""

    coordinator = runner.LCDRunner()
    coordinator.scroll_scheduler = FakeScheduler()
    coordinator.frame_writer = FakeWriter(object())
    coordinator.lcd = object()
    coordinator.event.display_state = SimpleNamespace(
        scroll_sec=0.25, steps1=2, steps2=2
    )

    shutdown_calls: list[object] = []
    monkeypatch.setattr(
        runner,
        "_handle_shutdown_request",
        lambda lcd: shutdown_calls.append(lcd) or True,
    )
    monkeypatch.setattr(
        runner,
        "_advance_display",
        lambda state, frame_writer, **kwargs: (state, True, True),
    )

    try:
        coordinator.render_event_frame()
    except StopIteration:
        pass
    else:  # pragma: no cover - defensive assertion path
        raise AssertionError("render_event_frame() should raise StopIteration")

    assert shutdown_calls == [coordinator.lcd]
    assert coordinator.scroll_scheduler.actions == [("sleep", None)]


def test_render_event_frame_refreshes_static_event_periodically(monkeypatch):
    """Static event frames should be redrawn while the event remains active."""

    coordinator = runner.LCDRunner()
    coordinator.scroll_scheduler = FakeScheduler()
    coordinator.frame_writer = FakeWriter(object())
    coordinator.lcd = object()
    coordinator.event.display_state = runner._prepare_display_state(
        "Codex ready",
        "Please review",
        0,
    )._replace(
        last_segment1="Codex ready     ",
        last_segment2="Please review   ",
    )
    coordinator.event.refresh_deadline = 5.0

    advanced_states: list[object] = []
    monkeypatch.setattr(runner.time, "monotonic", lambda: 5.0)

    def fake_advance_display(state, frame_writer, **kwargs):
        advanced_states.append(state)
        return state, True, False

    monkeypatch.setattr(runner, "_advance_display", fake_advance_display)

    handled = coordinator.render_event_frame()

    assert handled is True
    assert advanced_states
    assert advanced_states[0].last_segment1 is None
    assert advanced_states[0].last_segment2 is None
    assert coordinator.event.refresh_deadline == 7.0
    assert coordinator.scroll_scheduler.actions == [("sleep", None), ("advance", 0.5)]


def test_high_payloads_repeat_three_times_across_high_and_low_slots(monkeypatch):
    """HI payloads should display three times before advancing to the next payload."""

    coordinator = runner.LCDRunner()
    now_dt = datetime(2026, 3, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(
        runner,
        "_select_low_payload",
        lambda *args, **kwargs: runner.locks.LockPayload(
            "LO BASE", "", 0, is_base=True
        ),
    )

    high_cycle = runner.ChannelCycle(
        payloads=[
            runner.locks.LockPayload("HI-1", "", 0),
            runner.locks.LockPayload("HI-2", "", 0),
        ],
        signature=((0, 0.0), (1, 0.0)),
        index=0,
    )
    low_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {"high": high_cycle, "low": low_cycle}
    channel_text = {"high": True, "low": False}

    seen = [
        coordinator.payload_for_state(
            ("high", "low"), slot, channel_info, channel_text, now_dt
        ).line1
        for slot in (0, 1, 0, 1, 0, 1, 0)
    ]

    assert seen == ["HI-1", "HI-1", "HI-1", "HI-2", "HI-2", "HI-2", "HI-1"]

    high_cycle.signature = ((10, 0.0), (11, 0.0))
    high_cycle.payloads = [
        runner.locks.LockPayload("HI-NEW-1", "", 0),
        runner.locks.LockPayload("HI-NEW-2", "", 0),
    ]
    high_cycle.index = 1

    churn_seen = [
        coordinator.payload_for_state(
            ("high", "low"), slot, channel_info, channel_text, now_dt
        ).line1
        for slot in (0, 1, 0, 1)
    ]

    assert churn_seen == ["HI-NEW-1", "HI-NEW-1", "HI-NEW-1", "HI-NEW-2"]


def test_low_slot_keeps_default_when_only_one_high_payload_exists(monkeypatch):
    """Low slot should show its default base payload when only one HI payload exists."""

    coordinator = runner.LCDRunner()
    now_dt = datetime(2026, 3, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(
        runner,
        "_select_low_payload",
        lambda *args, **kwargs: runner.locks.LockPayload(
            "LO BASE", "", 0, is_base=True
        ),
    )

    high_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("HI-1", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    low_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {"high": high_cycle, "low": low_cycle}
    channel_text = {"high": True, "low": False}

    high_payload = coordinator.payload_for_state(
        ("high", "low"),
        0,
        channel_info,
        channel_text,
        now_dt,
    )
    low_payload = coordinator.payload_for_state(
        ("high", "low"),
        1,
        channel_info,
        channel_text,
        now_dt,
    )

    assert high_payload.line1 == "HI-1"
    assert low_payload.line1 == "LO BASE"


def test_low_slot_does_not_mirror_high_when_rotation_order_excludes_high(monkeypatch):
    """LO slot should respect configured channel order that excludes HI."""

    coordinator = runner.LCDRunner()
    now_dt = datetime(2026, 3, 20, tzinfo=timezone.utc)

    monkeypatch.setattr(
        runner,
        "_select_low_payload",
        lambda *args, **kwargs: runner.locks.LockPayload(
            "LO BASE", "", 0, is_base=True
        ),
    )

    high_cycle = runner.ChannelCycle(
        payloads=[
            runner.locks.LockPayload("HI-1", "", 0),
            runner.locks.LockPayload("HI-2", "", 0),
        ],
        signature=((0, 0.0), (1, 0.0)),
        index=0,
    )
    low_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {"high": high_cycle, "low": low_cycle}
    channel_text = {"high": True, "low": False}

    low_payload = coordinator.payload_for_state(
        ("low", "stats", "clock"),
        0,
        channel_info,
        channel_text,
        now_dt,
    )

    assert low_payload.line1 == "LO BASE"
    assert high_cycle.index == 0
    assert coordinator.high_repeat_count == 0


def test_rotation_order_interleaves_summary_channel_when_active(monkeypatch):
    coordinator = runner.LCDRunner()

    monkeypatch.setattr(runner.locks, "_load_channel_order", lambda lock_dir: None)

    payload_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("x", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {
        "high": payload_cycle,
        "low": payload_cycle,
        "summary": payload_cycle,
        "clock": payload_cycle,
        "stats": payload_cycle,
    }
    channel_text = {
        "high": True,
        "low": True,
        "summary": True,
        "clock": False,
        "stats": False,
    }

    coordinator.configure_rotation_order(channel_info, channel_text)

    assert coordinator.rotation.order == (
        "high",
        "summary",
        "low",
        "summary",
        "stats",
        "summary",
        "clock",
        "summary",
    )
    assert all(
        label == "summary"
        for index, label in enumerate(coordinator.rotation.order)
        if index % 2 == 1
    )


def test_interleaved_summary_order_preserves_duplicate_summary_index(monkeypatch):
    coordinator = runner.LCDRunner()
    coordinator.rotation.order = (
        "high",
        "summary",
        "low",
        "summary",
        "stats",
        "summary",
        "clock",
        "summary",
    )
    coordinator.rotation.index = 3

    monkeypatch.setattr(runner.locks, "_load_channel_order", lambda lock_dir: None)

    payload_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("x", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {
        "high": payload_cycle,
        "low": payload_cycle,
        "summary": payload_cycle,
        "clock": payload_cycle,
        "stats": payload_cycle,
    }
    channel_text = {
        "high": True,
        "low": True,
        "summary": True,
        "clock": False,
        "stats": False,
    }

    coordinator.configure_rotation_order(channel_info, channel_text)

    assert coordinator.rotation.index == 3


def test_low_channel_keeps_generated_base_with_lock_payloads(monkeypatch, tmp_path):
    now_dt = datetime(2026, 5, 4, tzinfo=timezone.utc)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("Status\n0 failed units\n", encoding="utf-8")

    monkeypatch.setattr(runner.locks, "LOCK_DIR", lock_dir)
    monkeypatch.setattr(
        runner,
        "_select_low_payload",
        lambda *args, **kwargs: runner.locks.LockPayload(
            "UP 0d1h1m", "ON 1m1s 88.2F", 0, is_base=True
        ),
    )

    channel_info, channel_text = runner._load_channel_states({}, now_dt)
    payload = runner.LCDRunner().payload_for_state(
        ("low",),
        0,
        channel_info,
        channel_text,
        now_dt,
    )

    assert channel_info["low"].payloads[0].is_base is True
    assert channel_info["low"].payloads[1].line1 == "Status"
    assert payload.line1 == "UP 0d1h1m"
    assert payload.line2 == "ON 1m1s 88.2F"


def test_low_channel_filters_routine_host_payloads_but_keeps_host_alerts(
    monkeypatch, tmp_path
):
    now_dt = datetime(2026, 5, 4, tzinfo=timezone.utc)
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "lcd-low").write_text("Status\n0 failed units\n", encoding="utf-8")
    (lock_dir / "lcd-low-1").write_text("Host\nt66C d51% m42%\n", encoding="utf-8")
    (lock_dir / "lcd-low-2").write_text(
        "Host\nfailed journal writer\n", encoding="utf-8"
    )
    (lock_dir / "lcd-low-3").write_text(
        "Host\nattention: thermal alert\n", encoding="utf-8"
    )

    monkeypatch.setattr(runner.locks, "LOCK_DIR", lock_dir)

    channel_info, _channel_text = runner._load_channel_states({}, now_dt)
    payloads = [
        (payload.line1, payload.line2) for payload in channel_info["low"].payloads
    ]

    assert ("Host", "t66C d51% m42%") not in payloads
    assert ("Host", "failed journal writer") in payloads
    assert ("Host", "attention: thermal alert") in payloads


def test_rotation_order_includes_usb_channel_when_active(monkeypatch):
    coordinator = runner.LCDRunner()

    monkeypatch.setattr(runner.locks, "_load_channel_order", lambda lock_dir: None)

    empty_cycle = runner.ChannelCycle(payloads=[], signature=(), index=0)
    payload_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("x", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {
        "high": empty_cycle,
        "low": payload_cycle,
        "summary": empty_cycle,
        "clock": empty_cycle,
        "stats": empty_cycle,
        "usb": payload_cycle,
    }
    channel_text = {
        "high": False,
        "low": True,
        "summary": False,
        "clock": False,
        "stats": False,
        "usb": True,
    }

    coordinator.configure_rotation_order(channel_info, channel_text)

    assert coordinator.rotation.order == ("low", "stats", "usb", "clock")


def test_configured_rotation_order_does_not_inject_summary_when_omitted(monkeypatch):
    coordinator = runner.LCDRunner()

    monkeypatch.setattr(
        runner.locks,
        "_load_channel_order",
        lambda lock_dir: ("high", "clock"),
    )

    payload_cycle = runner.ChannelCycle(
        payloads=[runner.locks.LockPayload("x", "", 0)],
        signature=((0, 0.0),),
        index=0,
    )
    channel_info = {
        "high": payload_cycle,
        "low": payload_cycle,
        "summary": payload_cycle,
        "clock": payload_cycle,
        "stats": payload_cycle,
    }
    channel_text = {
        "high": True,
        "low": True,
        "summary": True,
        "clock": True,
        "stats": False,
    }

    coordinator.configure_rotation_order(channel_info, channel_text)

    assert coordinator.rotation.order == ("high", "clock")


def test_summary_payload_rotates_its_own_channel() -> None:
    coordinator = runner.LCDRunner()
    now_dt = datetime(2026, 3, 20, tzinfo=timezone.utc)
    summary_cycle = runner.ChannelCycle(
        payloads=[
            runner.locks.LockPayload("SUM 1", "A", 0),
            runner.locks.LockPayload("SUM 2", "B", 0),
        ],
        signature=((0, 0.0), (1, 0.0)),
        index=0,
    )
    channel_info = {"summary": summary_cycle}
    channel_text = {"summary": True}

    first = coordinator.payload_for_state(
        ("summary",),
        0,
        channel_info,
        channel_text,
        now_dt,
    )
    second = coordinator.payload_for_state(
        ("summary",),
        0,
        channel_info,
        channel_text,
        now_dt,
    )

    assert (first.line1, second.line1) == ("SUM 1", "SUM 2")
