"""Runtime loop and rotation logic for the LCD screen service."""

from __future__ import annotations

import logging
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as datetime_timezone
from pathlib import Path

from apps.screens.history import LCDHistoryRecorder
from apps.screens.lcd import LCDUnavailableError

from . import locks
from .hardware import (
    LCDFrameWriter,
    LCDHealthMonitor,
    LCDWatchdog,
    _blank_display,
    _initialize_lcd,
)
from .logging import BASE_DIR
from .rendering import (
    DEFAULT_FALLBACK_SCROLL_SEC,
    ChannelCycle,
    DisplayState,
    ScrollScheduler,
    _advance_display,
    _clock_payload,
    _lcd_clock_enabled,
    _payload_has_text,
    _prepare_display_state,
    _refresh_uptime_payload,
    _select_low_payload,
    _stats_payload,
    _warn_on_non_ascii_payload,
)

logger = logging.getLogger(__name__)

ROTATION_SECONDS = 10
EVENT_LINE_SCROLL_SECONDS = 10
EVENT_STATIC_REFRESH_SECONDS = 2.0
BASE_RELIEF_BLOCKED_CYCLES = 3
BASE_RELIEF_LONG_EXPIRY = timedelta(seconds=ROTATION_SECONDS * BASE_RELIEF_BLOCKED_CYCLES)

_SHUTDOWN_REQUESTED = False
_EVENT_INTERRUPT_REQUESTED = False


@dataclass
class ChannelReliefState:
    """Track base-message relief cycles for sticky LCD channel payloads."""

    blocked_cycles: int = 0
    show_base_next: bool = False


@dataclass(frozen=True)
class PrefetchedCycle:
    """Represent a prepared display state for an upcoming rotation index."""

    order: tuple[str, ...]
    index: int
    display_state: DisplayState


@dataclass
class EventLoopState:
    """Track the currently active event payload and its scrolling state."""

    display_state: DisplayState | None = None
    payload: locks.EventPayload | None = None
    deadline: datetime | None = None
    lock_file: Path | None = None
    line_index: int = 0
    line_deadline: float = 0.0
    refresh_deadline: float = 0.0

    def reset(self) -> None:
        """Clear all active event state."""

        self.display_state = None
        self.payload = None
        self.deadline = None
        self.lock_file = None
        self.line_index = 0
        self.line_deadline = 0.0
        self.refresh_deadline = 0.0


@dataclass
class RotationState:
    """Track active and prefetched rotation display state."""

    display_state: DisplayState | None = None
    next_display_state: DisplayState | None = None
    deadline: float = 0.0
    order: tuple[str, ...] = ("high", "low", "stats", "clock")
    index: int = 0
    stats_cycle: int = 0
    clock_cycle: int = 0


@dataclass
class LCDRunner:
    """Coordinate LCD startup, event handling, rotation, and shutdown."""

    history_recorder: LCDHistoryRecorder = field(
        default_factory=lambda: LCDHistoryRecorder(base_dir=BASE_DIR, history_dir_name="work")
    )
    scroll_scheduler: ScrollScheduler = field(default_factory=ScrollScheduler)
    health: LCDHealthMonitor = field(default_factory=LCDHealthMonitor)
    watchdog: LCDWatchdog = field(default_factory=LCDWatchdog)
    frame_writer: LCDFrameWriter = field(init=False)
    lcd: object | None = None
    lcd_disabled: bool = False
    event: EventLoopState = field(default_factory=EventLoopState)
    rotation: RotationState = field(default_factory=RotationState)
    channel_states: dict[str, ChannelCycle] = field(default_factory=dict)
    relief_states: dict[str, ChannelReliefState] = field(default_factory=dict)
    cycle_prefetch_future: Future[PrefetchedCycle] | None = None
    cycle_prefetch_executor: ThreadPoolExecutor = field(
        default_factory=lambda: ThreadPoolExecutor(max_workers=1, thread_name_prefix="lcd-cycle")
    )
    cycle_state_lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        """Initialize the fallback frame writer."""

        self.frame_writer = LCDFrameWriter(None, history_recorder=self.history_recorder)

    def disable_lcd(
        self,
        reason: str,
        exc: Exception | None = None,
        *,
        exc_info: bool = False,
    ) -> None:
        """Disable LCD hardware use while preserving fallback output behavior."""

        _disable_lcd(self, reason, exc=exc, exc_info=exc_info)

    def register_signal_handlers(self) -> None:
        """Register signal handlers used by the runtime loop."""

        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
        signal.signal(signal.SIGHUP, _request_shutdown)
        signal.signal(signal.SIGUSR1, _request_event_interrupt)

    def initialize_hardware(self) -> None:
        """Initialize LCD hardware and frame writer if possible."""

        try:
            self.lcd = _initialize_lcd()
            self.frame_writer = LCDFrameWriter(self.lcd, history_recorder=self.history_recorder)
            self.health.record_success()
        except LCDUnavailableError as exc:
            self.disable_lcd("LCD unavailable during startup", exc)
        except Exception as exc:
            self.disable_lcd("LCD startup failed", exc, exc_info=True)

    def ensure_lcd(self) -> None:
        """Reinitialize LCD hardware when rendering requires it."""

        if self.lcd is not None:
            return
        self.lcd = _initialize_lcd()
        self.frame_writer = LCDFrameWriter(self.lcd, history_recorder=self.history_recorder)
        self.health.record_success()

    def setup(self) -> None:
        """Prepare signal handlers, lock cleanup, and initial LCD state."""

        locks._clear_low_lock_file()
        self.register_signal_handlers()
        self.initialize_hardware()

    def shutdown(self) -> None:
        """Release background resources and clear global signal flags."""

        self.cycle_prefetch_executor.shutdown(wait=True, cancel_futures=True)
        _blank_display(self.lcd)
        _reset_shutdown_flag()
        _reset_event_interrupt_flag()

    def reset_relief_state(self, label: str) -> None:
        """Reset the base-message relief counters for a channel."""

        relief_state = self.relief_states.setdefault(label, ChannelReliefState())
        relief_state.blocked_cycles = 0
        relief_state.show_base_next = False

    def apply_relief_if_needed(
        self,
        label: str,
        payload: locks.LockPayload | None,
        base_payload: locks.LockPayload,
        now_dt: datetime,
    ) -> locks.LockPayload:
        """Return payload adjusted for base-message relief cycles."""

        if not _sticky_payload(payload, now_dt):
            self.reset_relief_state(label)
            return payload or base_payload

        relief_state = self.relief_states.setdefault(label, ChannelReliefState())
        if relief_state.show_base_next:
            relief_state.show_base_next = False
            relief_state.blocked_cycles = 0
            return base_payload

        relief_state.blocked_cycles += 1
        if relief_state.blocked_cycles >= BASE_RELIEF_BLOCKED_CYCLES:
            relief_state.blocked_cycles = 0
            relief_state.show_base_next = True
        return payload or base_payload

    def load_channel_states(
        self,
        now_dt: datetime,
    ) -> tuple[dict[str, ChannelCycle], dict[str, bool]]:
        """Load per-channel payload cycles and visible-text metadata."""

        return _load_channel_states(self.channel_states, now_dt)

    def payload_for_state(
        self,
        state_order: tuple[str, ...],
        index: int,
        channel_info: dict[str, ChannelCycle],
        channel_text: dict[str, bool],
        now_dt: datetime,
        *,
        advance: bool = True,
    ) -> locks.LockPayload:
        """Resolve the payload that should be shown for a rotation slot."""

        state_label = state_order[index]
        channel_state = channel_info.get(state_label)

        def _peek_payload(cycle: ChannelCycle | None) -> locks.LockPayload | None:
            if cycle is None:
                return None
            if advance:
                return cycle.next_payload()
            if not cycle.payloads:
                return None
            return cycle.payloads[cycle.index % len(cycle.payloads)]

        if state_label == "high" and channel_state:
            payload = _peek_payload(channel_state)
            return payload or locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)
        if state_label in {"low", "uptime"} and channel_state:
            payload = _peek_payload(channel_state)
            base_payload = _select_low_payload(
                locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS),
                base_dir=BASE_DIR,
                now=now_dt,
            )
            if payload and _payload_has_text(payload):
                refreshed = _refresh_uptime_payload(payload, base_dir=BASE_DIR, now=now_dt)
                return self.apply_relief_if_needed("low", refreshed, base_payload, now_dt)
            self.reset_relief_state("low")
            return base_payload
        if state_label == "clock":
            if channel_state and channel_text[state_label]:
                payload = _peek_payload(channel_state)
                base_payload = _clock_base_payload(now_dt, use_fahrenheit=self.rotation.clock_cycle % 2 == 0)
                resolved = self.apply_relief_if_needed("clock", payload, base_payload, now_dt)
                if advance and resolved.is_base:
                    self.rotation.clock_cycle += 1
                return resolved
            if _lcd_clock_enabled():
                self.reset_relief_state("clock")
                use_fahrenheit = self.rotation.clock_cycle % 2 == 0
                base_payload = _clock_base_payload(now_dt, use_fahrenheit=use_fahrenheit)
                if advance:
                    self.rotation.clock_cycle += 1
                return base_payload
        if state_label == "stats":
            uptime_state = channel_info.get("uptime")
            uptime_payload = _peek_payload(uptime_state) or locks.LockPayload(
                "", "", locks.DEFAULT_SCROLL_MS
            )
            stats_payload = _peek_payload(channel_state)
            use_uptime = self.rotation.stats_cycle % 2 == 0
            if advance:
                self.rotation.stats_cycle += 1
            if use_uptime:
                if uptime_payload and _payload_has_text(uptime_payload):
                    return _refresh_uptime_payload(uptime_payload)
                return _select_low_payload(
                    locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS),
                    base_dir=BASE_DIR,
                    now=now_dt,
                )
            if stats_payload and _payload_has_text(stats_payload):
                base_payload = _stats_payload()
                return self.apply_relief_if_needed("stats", stats_payload, base_payload, now_dt)
            self.reset_relief_state("stats")
            return _stats_payload()
        return locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)

    def schedule_cycle_prefetch(
        self,
        order: tuple[str, ...],
        index: int,
        now_dt: datetime,
    ) -> None:
        """Prepare a future cycle in a worker thread to reduce rotation gap latency."""

        if self.cycle_prefetch_future and not self.cycle_prefetch_future.done():
            return

        def _prefetch() -> PrefetchedCycle:
            with self.cycle_state_lock:
                channel_info, channel_text = self.load_channel_states(now_dt)
                payload = self.payload_for_state(
                    order,
                    index,
                    channel_info,
                    channel_text,
                    now_dt,
                    advance=False,
                )
            _warn_on_non_ascii_payload(payload, order[index])
            prepared_state = _prepare_display_state(payload.line1, payload.line2, payload.scroll_ms)
            return PrefetchedCycle(order=order, index=index, display_state=prepared_state)

        self.cycle_prefetch_future = self.cycle_prefetch_executor.submit(_prefetch)

    def configure_rotation_order(
        self,
        channel_info: dict[str, ChannelCycle],
        channel_text: dict[str, bool],
    ) -> None:
        """Resolve the active channel rotation order from config and availability."""

        configured_order = locks._load_channel_order(locks.LOCK_DIR)

        def _channel_available(label: str) -> bool:
            if label == "high":
                return bool(channel_info[label].signature)
            if label == "clock":
                return channel_text[label] or _lcd_clock_enabled()
            if label in {"low", "stats"}:
                return True
            return False

        previous_order = self.rotation.order
        if configured_order:
            self.rotation.order = tuple(
                label for label in configured_order if _channel_available(label)
            )
            if not self.rotation.order:
                self.rotation.order = ("clock",)
        else:
            high_available = _channel_available("high")
            low_available = _channel_available("low")
            if high_available:
                self.rotation.order = (
                    ("high", "low", "stats", "clock")
                    if low_available
                    else ("high", "stats", "clock")
                )
            else:
                self.rotation.order = (
                    ("low", "stats", "clock") if low_available else ("stats", "clock")
                )

        if previous_order and 0 <= self.rotation.index < len(previous_order):
            current_label = previous_order[self.rotation.index]
            if current_label in self.rotation.order:
                self.rotation.index = self.rotation.order.index(current_label)
            else:
                self.rotation.index = 0
        else:
            self.rotation.index = 0

    def load_event_from_locks(self, now_dt: datetime) -> bool:
        """Load the next event payload into the runner, returning whether one exists."""

        payload, deadline, lock_file = _load_next_event(now_dt)
        self.event.payload = payload
        self.event.deadline = deadline
        self.event.lock_file = lock_file
        self.event.display_state = None
        self.event.line_index = 0
        self.event.line_deadline = 0.0
        return payload is not None

    def handle_external_event(self, now: float, now_dt: datetime) -> bool:
        """Update event state and render event frames when an event is active."""

        if _event_interrupt_requested():
            _reset_event_interrupt_flag()
            self.load_event_from_locks(now_dt)
        elif self.event.payload is None:
            self.load_event_from_locks(now_dt)

        if self.event.payload is None or self.event.deadline is None:
            return False

        if now_dt >= self.event.deadline:
            self.expire_current_event(now_dt)
            return True

        self.refresh_event_window(now, force_prepare=self.event.display_state is None)
        return self.render_event_frame()

    def expire_current_event(self, now_dt: datetime) -> None:
        """Expire the current event payload and resume normal rotation."""

        if self.event.lock_file:
            try:
                self.event.lock_file.unlink()
            except OSError:
                logger.debug(
                    "Failed to remove event lock file: %s",
                    self.event.lock_file,
                    exc_info=True,
                )
        if self.load_event_from_locks(now_dt):
            return
        self.event.reset()
        if self.rotation.order:
            self.rotation.index = (self.rotation.index + 1) % len(self.rotation.order)
        self.rotation.display_state = None
        self.rotation.next_display_state = None
        self.rotation.deadline = 0.0

    def refresh_event_window(self, now: float, *, force_prepare: bool = False) -> None:
        """Advance or prepare the visible two-line event window."""

        if self.event.payload is None:
            return
        if force_prepare:
            self._prepare_event_state(now)
            return
        if (
            len(self.event.payload.lines) > 2
            and self.event.line_deadline
            and now >= self.event.line_deadline
        ):
            max_index = max(len(self.event.payload.lines) - 2, 0)
            if self.event.line_index < max_index:
                self.event.line_index += 1
            self._prepare_event_state(now)

    def _prepare_event_state(self, now: float) -> None:
        """Build display state for the current event window."""

        if self.event.payload is None:
            return
        line1, line2 = _event_window(self.event.payload, self.event.line_index)
        self.event.display_state = _prepare_display_state(
            line1,
            line2,
            self.event.payload.scroll_ms,
        )
        self.event.line_deadline = (
            now + EVENT_LINE_SCROLL_SECONDS if len(self.event.payload.lines) > 2 else 0.0
        )
        self.event.refresh_deadline = 0.0

    def render_event_frame(self) -> bool:
        """Render the current event frame and record LCD health state."""

        self.ensure_lcd()
        self.scroll_scheduler.sleep_until_ready()
        frame_timestamp = datetime.now(datetime_timezone.utc)
        display_state = self.event.display_state
        refresh_now = time.monotonic()
        if (
            display_state is not None
            and display_state.steps1 == 1
            and display_state.steps2 == 1
            and (
                not self.event.refresh_deadline
                or refresh_now >= self.event.refresh_deadline
            )
        ):
            display_state = display_state._replace(last_segment1=None, last_segment2=None)
            self.event.refresh_deadline = refresh_now + EVENT_STATIC_REFRESH_SECONDS
        self.event.display_state, write_success, shutdown_triggered = _advance_display(
            display_state,
            self.frame_writer,
            shutdown_requested=_shutdown_requested,
            label="event",
            timestamp=frame_timestamp,
        )
        if shutdown_triggered:
            _handle_shutdown_request(self.lcd)
            raise StopIteration
        self.record_health(write_success, "LCD write failed during event display")
        self.scroll_scheduler.advance(
            (self.event.display_state.scroll_sec if self.event.display_state else 0)
            or DEFAULT_FALLBACK_SCROLL_SEC
        )
        return True

    def advance_rotation(self, now: float, now_dt: datetime) -> None:
        """Refresh rotation state when starting or changing display slots."""

        if self.rotation.display_state is not None and now < self.rotation.deadline:
            return

        with self.cycle_state_lock:
            channel_info, channel_text = self.load_channel_states(now_dt)
        self.configure_rotation_order(channel_info, channel_text)

        with self.cycle_state_lock:
            channel_info, channel_text = self.load_channel_states(now_dt)
            current_payload = self.payload_for_state(
                self.rotation.order,
                self.rotation.index,
                channel_info,
                channel_text,
                now_dt,
            )
        _warn_on_non_ascii_payload(current_payload, self.rotation.order[self.rotation.index])
        self.rotation.display_state = _prepare_display_state(
            current_payload.line1,
            current_payload.line2,
            current_payload.scroll_ms,
        )
        self.rotation.deadline = now + ROTATION_SECONDS

        if len(self.rotation.order) > 1:
            next_index = (self.rotation.index + 1) % len(self.rotation.order)
            with self.cycle_state_lock:
                channel_info, channel_text = self.load_channel_states(now_dt)
                next_payload = self.payload_for_state(
                    self.rotation.order,
                    next_index,
                    channel_info,
                    channel_text,
                    now_dt,
                )
            _warn_on_non_ascii_payload(next_payload, self.rotation.order[next_index])
            self.rotation.next_display_state = _prepare_display_state(
                next_payload.line1,
                next_payload.line2,
                next_payload.scroll_ms,
            )
            upcoming_index = (self.rotation.index + 2) % len(self.rotation.order)
            self.schedule_cycle_prefetch(self.rotation.order, upcoming_index, now_dt)
        else:
            self.rotation.next_display_state = None

    def render_rotation_frame(self) -> None:
        """Render the active rotation frame and record LCD health state."""

        if not self.rotation.display_state:
            self.scroll_scheduler.advance(DEFAULT_FALLBACK_SCROLL_SEC)
            self.scroll_scheduler.sleep_until_ready()
            return

        self.ensure_lcd()
        self.scroll_scheduler.sleep_until_ready()
        frame_timestamp = datetime.now(datetime_timezone.utc)
        label = self.rotation.order[self.rotation.index] if self.rotation.order else None
        self.rotation.display_state, write_success, shutdown_triggered = _advance_display(
            self.rotation.display_state,
            self.frame_writer,
            shutdown_requested=_shutdown_requested,
            label=label,
            timestamp=frame_timestamp,
        )
        if shutdown_triggered:
            _handle_shutdown_request(self.lcd)
            raise StopIteration
        self.record_health(write_success, "LCD write failed during rotation display")
        self.scroll_scheduler.advance(
            (self.rotation.display_state.scroll_sec if self.rotation.display_state else 0)
            or DEFAULT_FALLBACK_SCROLL_SEC
        )

    def finalize_rotation_step(self, now_dt: datetime) -> None:
        """Advance to the next rotation slot when the deadline passes."""

        if time.monotonic() < self.rotation.deadline:
            return
        if self.rotation.order:
            self.rotation.index = (self.rotation.index + 1) % len(self.rotation.order)
        if len(self.rotation.order) > 1:
            self.rotation.display_state = self.rotation.next_display_state
            next_index = (self.rotation.index + 1) % len(self.rotation.order)
            prefetched_cycle: PrefetchedCycle | None = None
            if self.cycle_prefetch_future and self.cycle_prefetch_future.done():
                try:
                    prefetched_cycle = self.cycle_prefetch_future.result()
                except Exception:
                    logger.exception("LCD cycle prefetch failed")
            if (
                prefetched_cycle is not None
                and prefetched_cycle.order == self.rotation.order
                and prefetched_cycle.index == next_index
            ):
                # Prefetched states are built without advancing the shared
                # channel cycle. Consume the matching slot here so numbered
                # LCD lock files rotate instead of sticking to the same entry.
                with self.cycle_state_lock:
                    channel_info, channel_text = self.load_channel_states(now_dt)
                    self.payload_for_state(
                        self.rotation.order,
                        next_index,
                        channel_info,
                        channel_text,
                        now_dt,
                    )
                self.rotation.next_display_state = prefetched_cycle.display_state
            else:
                with self.cycle_state_lock:
                    channel_info, channel_text = self.load_channel_states(now_dt)
                    next_payload = self.payload_for_state(
                        self.rotation.order,
                        next_index,
                        channel_info,
                        channel_text,
                        now_dt,
                    )
                _warn_on_non_ascii_payload(next_payload, self.rotation.order[next_index])
                self.rotation.next_display_state = _prepare_display_state(
                    next_payload.line1,
                    next_payload.line2,
                    next_payload.scroll_ms,
                )
            self.cycle_prefetch_future = None
            upcoming_index = (self.rotation.index + 2) % len(self.rotation.order)
            self.schedule_cycle_prefetch(self.rotation.order, upcoming_index, now_dt)
        else:
            with self.cycle_state_lock:
                channel_info, channel_text = self.load_channel_states(now_dt)
                current_payload = self.payload_for_state(
                    self.rotation.order,
                    self.rotation.index,
                    channel_info,
                    channel_text,
                    now_dt,
                )
            self.rotation.display_state = _prepare_display_state(
                current_payload.line1,
                current_payload.line2,
                current_payload.scroll_ms,
            )
            self.rotation.next_display_state = None
        self.rotation.deadline = time.monotonic() + ROTATION_SECONDS

    def record_health(self, write_success: bool, disable_reason: str) -> None:
        """Update health/backoff state after a frame write attempt."""

        if write_success:
            self.health.record_success()
            if self.lcd and self.watchdog.tick():
                self.lcd.reset()
                self.watchdog.reset()
            return
        if self.lcd is not None and self.frame_writer.lcd is None:
            self.disable_lcd(disable_reason)
            return
        delay = self.health.record_failure()
        time.sleep(delay)

    def run(self) -> None:
        """Execute the LCD service loop until shutdown is requested."""

        self.setup()
        try:
            while True:
                if _handle_shutdown_request(self.lcd):
                    break
                if self.lcd_disabled:
                    self.scroll_scheduler.advance(DEFAULT_FALLBACK_SCROLL_SEC)
                    self.scroll_scheduler.sleep_until_ready()
                    continue
                try:
                    now = time.monotonic()
                    now_dt = datetime.now(datetime_timezone.utc)
                    if self.handle_external_event(now, now_dt):
                        continue
                    self.advance_rotation(now, now_dt)
                    self.render_rotation_frame()
                    self.finalize_rotation_step(now_dt)
                except StopIteration:
                    break
                except LCDUnavailableError as exc:
                    self.disable_lcd("LCD unavailable", exc)
                except Exception:
                    logger.exception(
                        "Unexpected error while updating LCD state; keeping LCD enabled"
                    )
        finally:
            self.shutdown()


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    """Return whether the process has received a shutdown signal."""

    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    """Reset the shutdown signal flag for tests or reruns."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def _request_event_interrupt(signum, frame) -> None:  # pragma: no cover - signal handler
    """Interrupt the LCD cycle to show event lock files immediately."""

    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = True


def _event_interrupt_requested() -> bool:
    """Return whether an event interrupt has been requested."""

    return _EVENT_INTERRUPT_REQUESTED


def _reset_event_interrupt_flag() -> None:
    """Reset the event interrupt flag for tests or reruns."""

    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = False


def _sticky_payload(payload: locks.LockPayload | None, now_dt: datetime) -> bool:
    """Return True when a payload should trigger base-message relief."""

    if payload is None or payload.is_base or not _payload_has_text(payload):
        return False
    if payload.expires_at is None:
        return True
    return payload.expires_at - now_dt >= BASE_RELIEF_LONG_EXPIRY


def _handle_shutdown_request(lcd) -> bool:
    """Blank the display and signal the loop to exit when shutting down."""

    if not _shutdown_requested():
        return False
    _blank_display(lcd)
    return True


def _clock_base_payload(now_dt: datetime, *, use_fahrenheit: bool) -> locks.LockPayload:
    """Build the base clock payload when the clock channel is enabled."""

    if not _lcd_clock_enabled():
        return locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS, is_base=True)
    line1, line2, speed, _ = _clock_payload(
        now_dt.astimezone(), use_fahrenheit=use_fahrenheit
    )
    return locks.LockPayload(line1, line2, speed, is_base=True)


def _disable_lcd(
    runner: LCDRunner,
    reason: str,
    exc: Exception | None = None,
    *,
    exc_info: bool = False,
) -> None:
    """Disable LCD hardware use while keeping fallback frame capture active."""

    if runner.lcd_disabled:
        return
    runner.lcd_disabled = True
    if exc is None:
        logger.warning("Disabling LCD feature: %s", reason)
    else:
        logger.warning("Disabling LCD feature: %s: %s", reason, exc, exc_info=exc_info)
    _blank_display(runner.lcd)
    runner.lcd = None
    runner.rotation.display_state = None
    runner.rotation.next_display_state = None
    runner.event.reset()
    runner.frame_writer = LCDFrameWriter(None, history_recorder=runner.history_recorder)


def _load_channel_states(
    current_states: dict[str, ChannelCycle],
    now_dt: datetime,
) -> tuple[dict[str, ChannelCycle], dict[str, bool]]:
    """Load channel cycles from lock files while preserving rotation indices."""

    channel_info: dict[str, ChannelCycle] = {}
    channel_text: dict[str, bool] = {}
    for label, base_name in locks.CHANNEL_BASE_NAMES.items():
        entries = locks._channel_lock_entries(locks.LOCK_DIR, base_name)
        existing = current_states.get(label)
        signature = tuple((num, mtime) for num, _, mtime in entries)
        payloads: list[locks.LockPayload] = []
        if label == "low":
            payloads, has_base_payload = locks._load_low_channel_payloads(entries, now=now_dt)
            if not has_base_payload:
                payloads.insert(0, locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS))
                signature = ((0, -1.0),) + signature
        else:
            payloads = locks._load_channel_payloads(entries, now=now_dt)
        if existing is None or existing.signature != signature or payloads != existing.payloads:
            next_index = 0
            if existing and payloads:
                next_index = existing.index % len(payloads)
            existing = ChannelCycle(payloads=payloads, signature=signature, index=next_index)
        current_states[label] = existing
        channel_info[label] = existing
        channel_text[label] = any(_payload_has_text(payload) for payload in existing.payloads)
    return channel_info, channel_text


def _load_next_event(
    now_dt: datetime,
) -> tuple[locks.EventPayload | None, datetime | None, Path | None]:
    """Return the next event payload, expiry, and lock file path."""

    for candidate in locks._event_lock_files():
        try:
            payload, expires_at = locks._parse_event_lock_file(candidate, now_dt)
        except FileNotFoundError:
            continue
        except OSError:
            continue
        if expires_at <= now_dt:
            try:
                candidate.unlink()
            except OSError:
                logger.debug("Failed to remove expired event lock: %s", candidate, exc_info=True)
            continue
        return payload, expires_at, candidate
    return None, None, None


def _event_window(payload: locks.EventPayload, index: int) -> tuple[str, str]:
    """Return a two-line window for the event payload starting at index."""

    if not payload.lines:
        return "", ""
    line1 = payload.lines[index] if index < len(payload.lines) else ""
    line2 = payload.lines[index + 1] if index + 1 < len(payload.lines) else ""
    return line1, line2


def main() -> None:  # pragma: no cover - hardware dependent
    """Run the LCD service using the default coordinator."""

    LCDRunner().run()


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    main()
