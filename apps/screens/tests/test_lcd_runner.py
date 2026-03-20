"""Tests for the LCD runner coordinator helpers."""

from __future__ import annotations

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
    monkeypatch.setattr(runner, "_reset_event_interrupt_flag", lambda: reset_calls.append("reset"))
    monkeypatch.setattr(runner, "_load_next_event", lambda now_dt: (payload, event_deadline, lock_file))
    monkeypatch.setattr(
        runner,
        "_prepare_display_state",
        lambda line1, line2, scroll_ms: SimpleNamespace(
            line1=line1,
            line2=line2,
            scroll_ms=scroll_ms,
            scroll_sec=0.25,
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
