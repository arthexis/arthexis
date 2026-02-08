"""Runtime loop and rotation logic for the LCD screen service."""

from __future__ import annotations

import logging
import signal
import time
from datetime import datetime, timezone as datetime_timezone
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
    ChannelCycle,
    DEFAULT_FALLBACK_SCROLL_SEC,
    DisplayState,
    GAP_ANIMATION_CYCLE,
    GAP_ANIMATION_SCROLL_MS,
    ScrollScheduler,
    _clock_payload,
    _lcd_clock_enabled,
    _payload_has_text,
    _prepare_display_state,
    _refresh_uptime_payload,
    _select_low_payload,
    _stats_payload,
    _warn_on_non_ascii_payload,
    _advance_display,
)

logger = logging.getLogger(__name__)

ROTATION_SECONDS = 10
EVENT_LINE_SCROLL_SECONDS = 10

_SHUTDOWN_REQUESTED = False
_EVENT_INTERRUPT_REQUESTED = False


def _request_shutdown(signum, frame) -> None:  # pragma: no cover - signal handler
    """Mark the loop for shutdown when the process receives a signal."""

    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = True


def _shutdown_requested() -> bool:
    return _SHUTDOWN_REQUESTED


def _reset_shutdown_flag() -> None:
    global _SHUTDOWN_REQUESTED
    _SHUTDOWN_REQUESTED = False


def _request_event_interrupt(signum, frame) -> None:  # pragma: no cover - signal handler
    """Interrupt the LCD cycle to show event lock files immediately."""

    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = True


def _event_interrupt_requested() -> bool:
    return _EVENT_INTERRUPT_REQUESTED


def _reset_event_interrupt_flag() -> None:
    global _EVENT_INTERRUPT_REQUESTED
    _EVENT_INTERRUPT_REQUESTED = False


def _handle_shutdown_request(lcd) -> bool:
    """Blank the display and signal the loop to exit when shutting down."""

    if not _shutdown_requested():
        return False

    _blank_display(lcd)
    return True


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
                logger.debug(
                    "Failed to remove expired event lock: %s",
                    candidate,
                    exc_info=True,
                )
            continue
        return payload, expires_at, candidate
    return None, None, None


def _event_window(payload: locks.EventPayload, index: int) -> tuple[str, str]:
    """Return a two-line window for the event payload starting at *index*."""
    if not payload.lines:
        return "", ""
    line1 = payload.lines[index] if index < len(payload.lines) else ""
    line2 = payload.lines[index + 1] if index + 1 < len(payload.lines) else ""
    return line1, line2


def main() -> None:  # pragma: no cover - hardware dependent
    lcd = None
    display_state: DisplayState | None = None
    next_display_state: DisplayState | None = None
    event_state: DisplayState | None = None
    event_payload: locks.EventPayload | None = None
    event_deadline: datetime | None = None
    event_lock_file: Path | None = None
    event_line_index = 0
    event_line_deadline = 0.0
    rotation_deadline = 0.0
    scroll_scheduler = ScrollScheduler()
    state_order = ("high", "low", "stats", "clock")
    state_index = 0
    stats_cycle = 0
    history_recorder = LCDHistoryRecorder(base_dir=BASE_DIR, history_dir_name="work")
    clock_cycle = 0
    health = LCDHealthMonitor()
    watchdog = LCDWatchdog()
    channel_states: dict[str, ChannelCycle] = {}
    frame_writer: LCDFrameWriter = LCDFrameWriter(None, history_recorder=history_recorder)
    lcd_disabled = False
    locks._clear_low_lock_file()

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    signal.signal(signal.SIGHUP, _request_shutdown)
    signal.signal(signal.SIGUSR1, _request_event_interrupt)

    def _disable_lcd(
        reason: str, exc: Exception | None = None, *, exc_info: bool = False
    ) -> None:
        nonlocal lcd
        nonlocal display_state
        nonlocal next_display_state
        nonlocal event_state
        nonlocal event_payload
        nonlocal event_deadline
        nonlocal event_lock_file
        nonlocal event_line_index
        nonlocal event_line_deadline
        nonlocal frame_writer
        nonlocal lcd_disabled
        if lcd_disabled:
            return
        lcd_disabled = True
        if exc is None:
            logger.warning("Disabling LCD feature: %s", reason)
        else:
            logger.warning(
                "Disabling LCD feature: %s: %s", reason, exc, exc_info=exc_info
            )
        _blank_display(lcd)
        lcd = None
        display_state = None
        next_display_state = None
        event_state = None
        event_payload = None
        event_deadline = None
        event_lock_file = None
        event_line_index = 0
        event_line_deadline = 0.0
        frame_writer = LCDFrameWriter(None, history_recorder=history_recorder)

    def _load_channel_states(
        now_dt: datetime,
    ) -> tuple[dict[str, ChannelCycle], dict[str, bool]]:
        channel_info: dict[str, ChannelCycle] = {}
        channel_text: dict[str, bool] = {}
        for label, base_name in locks.CHANNEL_BASE_NAMES.items():
            entries = locks._channel_lock_entries(locks.LOCK_DIR, base_name)
            existing = channel_states.get(label)
            signature = tuple((num, mtime) for num, _, mtime in entries)
            payloads: list[locks.LockPayload] = []
            if label == "low":
                payloads, has_base_payload = locks._load_low_channel_payloads(
                    entries, now=now_dt
                )
                if not has_base_payload:
                    payloads.insert(0, locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS))
                    signature = ((0, -1.0),) + signature
            else:
                payloads = locks._load_channel_payloads(entries, now=now_dt)
            if (
                existing is None
                or existing.signature != signature
                or payloads != existing.payloads
            ):
                next_index = 0
                if existing and payloads:
                    next_index = existing.index % len(payloads)
                existing = ChannelCycle(
                    payloads=payloads,
                    signature=signature,
                    index=next_index,
                )
            channel_states[label] = existing
            channel_info[label] = existing
            channel_text[label] = any(
                _payload_has_text(payload) for payload in existing.payloads
            )
        return channel_info, channel_text

    def _payload_for_state(
        state_order: tuple[str, ...],
        index: int,
        channel_info: dict[str, ChannelCycle],
        channel_text: dict[str, bool],
        now_dt: datetime,
        *,
        advance: bool = True,
    ) -> locks.LockPayload:
        nonlocal clock_cycle
        nonlocal stats_cycle
        state_label = state_order[index]
        channel_state = channel_info.get(state_label)
        if state_label == "high" and channel_state:
            payload = (
                channel_state.next_payload()
                if advance
                else channel_state.payloads[0]
                if channel_state.payloads
                else None
            )
            return payload or locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)
        if state_label in {"low", "uptime"} and channel_state:
            payload = (
                channel_state.next_payload()
                if advance
                else channel_state.payloads[0]
                if channel_state.payloads
                else None
            )
            if payload and _payload_has_text(payload):
                return _refresh_uptime_payload(payload)
            return _select_low_payload(
                locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS),
                base_dir=BASE_DIR,
                now=now_dt,
            )
        if state_label == "clock":
            if channel_state and channel_text[state_label]:
                payload = (
                    channel_state.next_payload()
                    if advance
                    else channel_state.payloads[0]
                    if channel_state.payloads
                    else None
                )
                return payload or locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)
            if _lcd_clock_enabled():
                use_fahrenheit = clock_cycle % 2 == 0
                line1, line2, speed, _ = _clock_payload(
                    now_dt.astimezone(), use_fahrenheit=use_fahrenheit
                )
                clock_cycle += 1
                return locks.LockPayload(line1, line2, speed)
        if state_label == "stats":
            uptime_state = channel_info.get("uptime")
            uptime_payload = (
                uptime_state.next_payload()
                if uptime_state and advance
                else uptime_state.payloads[0]
                if uptime_state and uptime_state.payloads
                else locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)
            )
            stats_payload = (
                channel_state.next_payload()
                if channel_state and advance
                else channel_state.payloads[0]
                if channel_state and channel_state.payloads
                else None
            )
            use_uptime = stats_cycle % 2 == 0
            stats_cycle += 1
            if use_uptime:
                if uptime_payload and _payload_has_text(uptime_payload):
                    return _refresh_uptime_payload(uptime_payload)
                return _select_low_payload(
                    locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS),
                    base_dir=BASE_DIR,
                    now=now_dt,
                )
            if stats_payload and _payload_has_text(stats_payload):
                return stats_payload
            return _stats_payload()
        return locks.LockPayload("", "", locks.DEFAULT_SCROLL_MS)

    try:
        try:
            lcd = _initialize_lcd()
            frame_writer = LCDFrameWriter(lcd, history_recorder=history_recorder)
            health.record_success()
        except LCDUnavailableError as exc:
            _disable_lcd("LCD unavailable during startup", exc)
        except Exception as exc:
            _disable_lcd("LCD startup failed", exc, exc_info=True)

        while True:
            if _handle_shutdown_request(lcd):
                break
            if lcd_disabled:
                scroll_scheduler.advance(DEFAULT_FALLBACK_SCROLL_SEC)
                scroll_scheduler.sleep_until_ready()
                continue

            try:
                now = time.monotonic()
                now_dt = datetime.now(datetime_timezone.utc)

                if _event_interrupt_requested():
                    _reset_event_interrupt_flag()
                    (
                        event_payload,
                        event_deadline,
                        event_lock_file,
                    ) = _load_next_event(now_dt)
                    event_state = None
                    event_line_index = 0
                    event_line_deadline = 0.0
                elif event_payload is None:
                    (
                        pending_payload,
                        pending_deadline,
                        pending_lock_file,
                    ) = _load_next_event(now_dt)
                    if pending_payload is not None:
                        event_payload = pending_payload
                        event_deadline = pending_deadline
                        event_lock_file = pending_lock_file
                        event_state = None
                        event_line_index = 0
                        event_line_deadline = 0.0

                if event_payload is not None and event_deadline is not None:
                    if now_dt >= event_deadline:
                        if event_lock_file:
                            try:
                                event_lock_file.unlink()
                            except OSError:
                                logger.debug(
                                    "Failed to remove event lock file: %s",
                                    event_lock_file,
                                    exc_info=True,
                                )
                        (
                            event_payload,
                            event_deadline,
                            event_lock_file,
                        ) = _load_next_event(now_dt)
                        if event_payload is not None:
                            event_state = None
                            event_line_index = 0
                            event_line_deadline = 0.0
                            continue
                        event_payload = None
                        event_state = None
                        event_deadline = None
                        event_lock_file = None
                        event_line_index = 0
                        event_line_deadline = 0.0
                        if state_order:
                            state_index = (state_index + 1) % len(state_order)
                        display_state = None
                        next_display_state = None
                        rotation_deadline = 0.0
                        continue

                    if event_state is None and event_payload is not None:
                        line1, line2 = _event_window(event_payload, event_line_index)
                        event_state = _prepare_display_state(
                            line1, line2, event_payload.scroll_ms
                        )
                        if len(event_payload.lines) > 2:
                            event_line_deadline = now + EVENT_LINE_SCROLL_SECONDS
                        else:
                            event_line_deadline = 0.0

                    if (
                        event_payload is not None
                        and len(event_payload.lines) > 2
                        and event_line_deadline
                        and now >= event_line_deadline
                    ):
                        max_index = max(len(event_payload.lines) - 2, 0)
                        if event_line_index < max_index:
                            event_line_index += 1
                        line1, line2 = _event_window(event_payload, event_line_index)
                        event_state = _prepare_display_state(
                            line1, line2, event_payload.scroll_ms
                        )
                        event_line_deadline = now + EVENT_LINE_SCROLL_SECONDS

                    if lcd is None:
                        lcd = _initialize_lcd()
                        frame_writer = LCDFrameWriter(
                            lcd, history_recorder=history_recorder
                        )
                        health.record_success()

                    scroll_scheduler.sleep_until_ready()
                    frame_timestamp = datetime.now(datetime_timezone.utc)
                    event_state, write_success, shutdown_triggered = _advance_display(
                        event_state,
                        frame_writer,
                        shutdown_requested=_shutdown_requested,
                        label="event",
                        timestamp=frame_timestamp,
                    )
                    if shutdown_triggered:
                        _handle_shutdown_request(lcd)
                        break
                    if write_success:
                        health.record_success()
                        if lcd and watchdog.tick():
                            lcd.reset()
                            watchdog.reset()
                    else:
                        if lcd is not None and frame_writer.lcd is None:
                            _disable_lcd("LCD write failed during event display")
                            continue
                        delay = health.record_failure()
                        time.sleep(delay)
                    scroll_scheduler.advance(
                        (event_state.scroll_sec if event_state else 0)
                        or DEFAULT_FALLBACK_SCROLL_SEC
                    )
                    continue

                if display_state is None or now >= rotation_deadline:
                    channel_info, channel_text = _load_channel_states(now_dt)

                    configured_order = locks._load_channel_order(locks.LOCK_DIR)

                    def _channel_available(label: str) -> bool:
                        if label == "high":
                            return bool(channel_info[label].signature)
                        if label == "clock":
                            return channel_text[label] or _lcd_clock_enabled()
                        if label == "low":
                            return True
                        if label == "stats":
                            return True
                        return False

                    previous_order = state_order
                    if configured_order:
                        state_order = tuple(
                            label
                            for label in configured_order
                            if _channel_available(label)
                        )
                        if not state_order:
                            state_order = ("clock",)
                    else:
                        high_available = _channel_available("high")
                        low_available = _channel_available("low")
                        if high_available:
                            state_order = (
                                ("high", "low", "stats", "clock")
                                if low_available
                                else ("high", "stats", "clock")
                            )
                        else:
                            state_order = (
                                ("low", "stats", "clock")
                                if low_available
                                else ("stats", "clock")
                            )

                    if previous_order and 0 <= state_index < len(previous_order):
                        current_label = previous_order[state_index]
                        if current_label in state_order:
                            state_index = state_order.index(current_label)
                        else:
                            state_index = 0
                    else:
                        state_index = 0

                    current_payload = _payload_for_state(
                        state_order,
                        state_index,
                        channel_info,
                        channel_text,
                        now_dt,
                    )
                    _warn_on_non_ascii_payload(current_payload, state_order[state_index])
                    display_state = _prepare_display_state(
                        current_payload.line1,
                        current_payload.line2,
                        current_payload.scroll_ms,
                    )
                    rotation_deadline = now + ROTATION_SECONDS

                    if len(state_order) > 1:
                        next_index = (state_index + 1) % len(state_order)
                        next_payload = _payload_for_state(
                            state_order,
                            next_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        _warn_on_non_ascii_payload(
                            next_payload, state_order[next_index]
                        )
                        next_display_state = _prepare_display_state(
                            next_payload.line1,
                            next_payload.line2,
                            next_payload.scroll_ms,
                        )
                    else:
                        next_display_state = None

                if lcd is None:
                    lcd = _initialize_lcd()
                    frame_writer = LCDFrameWriter(lcd, history_recorder=history_recorder)
                    health.record_success()

                if display_state and frame_writer:
                    scroll_scheduler.sleep_until_ready()
                    frame_timestamp = datetime.now(datetime_timezone.utc)
                    label = state_order[state_index] if state_order else None
                    display_state, write_success, shutdown_triggered = _advance_display(
                        display_state,
                        frame_writer,
                        shutdown_requested=_shutdown_requested,
                        label=label,
                        timestamp=frame_timestamp,
                    )
                    if shutdown_triggered:
                        _handle_shutdown_request(lcd)
                        break
                    next_scroll_sec = display_state.scroll_sec
                    if write_success:
                        health.record_success()
                        if lcd and watchdog.tick():
                            lcd.reset()
                            watchdog.reset()
                    else:
                        if lcd is not None and frame_writer.lcd is None:
                            _disable_lcd("LCD write failed during rotation display")
                            continue
                        delay = health.record_failure()
                        time.sleep(delay)
                    scroll_scheduler.advance(
                        next_scroll_sec or DEFAULT_FALLBACK_SCROLL_SEC
                    )
                else:
                    scroll_scheduler.advance(DEFAULT_FALLBACK_SCROLL_SEC)
                    scroll_scheduler.sleep_until_ready()

                if time.monotonic() >= rotation_deadline:
                    if state_order:
                        state_index = (state_index + 1) % len(state_order)
                    if len(state_order) > 1:
                        display_state = next_display_state

                        # Prepare the following state in advance for predictable timing.
                        channel_info, channel_text = _load_channel_states(now_dt)
                        next_index = (state_index + 1) % len(state_order)
                        next_payload = _payload_for_state(
                            state_order,
                            next_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        next_display_state = _prepare_display_state(
                            next_payload.line1,
                            next_payload.line2,
                            next_payload.scroll_ms,
                        )
                    else:
                        channel_info, channel_text = _load_channel_states(now_dt)
                        current_payload = _payload_for_state(
                            state_order,
                            state_index,
                            channel_info,
                            channel_text,
                            now_dt,
                        )
                        display_state = _prepare_display_state(
                            current_payload.line1,
                            current_payload.line2,
                            current_payload.scroll_ms,
                        )
                        next_display_state = None
                    rotation_deadline = time.monotonic() + ROTATION_SECONDS
            except LCDUnavailableError as exc:
                _disable_lcd("LCD unavailable", exc)
                continue
            except Exception:
                logger.exception(
                    "Unexpected error while updating LCD state; keeping LCD enabled"
                )
                continue

    finally:
        _blank_display(lcd)
        _reset_shutdown_flag()
        _reset_event_interrupt_flag()


if __name__ == "__main__":  # pragma: no cover - module entrypoint
    main()
