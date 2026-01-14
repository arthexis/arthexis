from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.screens import lcd_screen
from apps.screens.startup_notifications import LCD_HIGH_LOCK_FILE, LCD_LOW_LOCK_FILE


@dataclass(frozen=True)
class PlannedFrame:
    offset: float
    timestamp: datetime
    label: str
    line1: str
    line2: str


class PlanFrameWriter:
    def __init__(self) -> None:
        self._last_frame: PlannedFrame | None = None

    def write(
        self,
        line1: str,
        line2: str,
        *,
        label: str | None = None,
        timestamp: datetime | None = None,
    ) -> bool:
        if label is None or timestamp is None:
            raise ValueError("PlanFrameWriter requires label and timestamp")
        self._last_frame = PlannedFrame(0.0, timestamp, label, line1, line2)
        return True

    def pop_frame(self) -> PlannedFrame | None:
        frame = self._last_frame
        self._last_frame = None
        return frame


class Command(BaseCommand):
    help = "Preview the next 60 seconds of LCD output from current lock files"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--seconds",
            type=int,
            default=60,
            help="How many seconds to predict (default: 60)",
        )
        parser.add_argument(
            "-i",
            "--interactive",
            action="store_true",
            help="Emit frames at real-time cadence for live testing",
        )

    def handle(self, *args, **options) -> None:
        duration = options["seconds"]
        interactive = options["interactive"]
        base_dir = Path(settings.BASE_DIR)
        start_dt = self._now()

        frames = self._iter_frames(
            duration=duration,
            base_dir=base_dir,
            start_dt=start_dt,
        )

        if not interactive:
            self.stdout.write(
                f"LCD plan starting at {start_dt.isoformat()} for {duration}s (base: {base_dir})"
            )

        start_monotonic = self._monotonic()
        for frame in frames:
            if interactive:
                target = start_monotonic + frame.offset
                delay = target - self._monotonic()
                if delay > 0:
                    self._sleep(delay)
            self.stdout.write(self._format_frame(frame, start_dt))

    def _format_frame(self, frame: PlannedFrame, start_dt: datetime) -> str:
        offset = frame.timestamp - start_dt
        offset_seconds = offset.total_seconds()
        return (
            f"T+{offset_seconds:05.1f}s [{frame.label}] {frame.line1} | {frame.line2}"
        )

    def _iter_frames(
        self,
        *,
        duration: int,
        base_dir: Path,
        start_dt: datetime,
    ) -> Iterable[PlannedFrame]:
        lock_dir = base_dir / ".locks"
        high_lock_file = lock_dir / LCD_HIGH_LOCK_FILE
        low_lock_file = lock_dir / LCD_LOW_LOCK_FILE
        event_candidates = self._event_lock_files(lock_dir)
        event_index = 0

        def load_next_event(now_dt: datetime) -> tuple[lcd_screen.LockPayload | None, datetime | None]:
            nonlocal event_index
            while event_index < len(event_candidates):
                candidate = event_candidates[event_index]
                event_index += 1
                try:
                    payload, expires_at = lcd_screen._parse_event_lock_file(
                        candidate, now_dt
                    )
                except (FileNotFoundError, OSError):
                    continue
                if expires_at <= now_dt:
                    continue
                return payload, expires_at
            return None, None

        def read_lock_payload(lock_file: Path, now_dt: datetime) -> lcd_screen.LockPayload:
            payload = lcd_screen.read_lcd_lock_file(lock_file)
            if payload is None:
                return lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)
            if payload.expires_at and payload.expires_at <= now_dt:
                return lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)
            return lcd_screen.LockPayload(payload.subject, payload.body, lcd_screen.DEFAULT_SCROLL_MS)

        def low_payload_fallback(
            payload: lcd_screen.LockPayload, now_dt: datetime
        ) -> lcd_screen.LockPayload:
            return lcd_screen._select_low_payload(
                payload,
                frame_cycle=lcd_screen.GAP_ANIMATION_CYCLE,
                base_dir=base_dir,
                now=now_dt,
                scroll_ms=lcd_screen.GAP_ANIMATION_SCROLL_MS,
            )

        def payload_for_state(
            index: int,
            *,
            now_dt: datetime,
            high_payload: lcd_screen.LockPayload,
            low_payload: lcd_screen.LockPayload,
            clock_cycle: int,
            state_order: tuple[str, ...],
        ) -> tuple[lcd_screen.LockPayload, int]:
            state_label = state_order[index]
            if state_label == "high":
                return high_payload, clock_cycle
            if state_label == "low":
                return (
                    lcd_screen._refresh_uptime_payload(
                        low_payload, base_dir=base_dir, now=now_dt
                    ),
                    clock_cycle,
                )
            if lcd_screen._lcd_clock_enabled():
                use_fahrenheit = clock_cycle % 2 == 0
                local_now = now_dt.astimezone() if now_dt.tzinfo else now_dt
                line1, line2, speed, _ = lcd_screen._clock_payload(
                    local_now, use_fahrenheit=use_fahrenheit
                )
                return lcd_screen.LockPayload(line1, line2, speed), clock_cycle + 1
            return lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS), clock_cycle

        def compute_state_order(
            high_available: bool,
            low_available: bool,
        ) -> tuple[str, ...]:
            if high_available:
                return ("high", "low", "clock") if low_available else ("high", "clock")
            return ("low", "clock") if low_available else ("clock",)

        def load_payloads(
            now_dt: datetime,
        ) -> tuple[lcd_screen.LockPayload, lcd_screen.LockPayload, bool, bool]:
            high_available = high_lock_file.exists()
            low_available = low_lock_file.exists()
            high_payload = (
                read_lock_payload(high_lock_file, now_dt)
                if high_available
                else lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)
            )
            low_payload = (
                read_lock_payload(low_lock_file, now_dt)
                if low_available
                else lcd_screen.LockPayload("", "", lcd_screen.DEFAULT_SCROLL_MS)
            )
            low_payload = low_payload_fallback(low_payload, now_dt)
            low_available = lcd_screen._payload_has_text(low_payload)
            return high_payload, low_payload, high_available, low_available

        def refresh_state_order(high_available: bool, low_available: bool) -> None:
            nonlocal state_order, state_index
            previous_order = state_order
            state_order = compute_state_order(high_available, low_available)
            if previous_order and 0 <= state_index < len(previous_order):
                current_label = previous_order[state_index]
                if current_label in state_order:
                    state_index = state_order.index(current_label)
                else:
                    state_index = 0
            else:
                state_index = 0

        writer = PlanFrameWriter()
        clock_cycle = 0
        state_index = 0
        state_order: tuple[str, ...] = ("high", "low", "clock")
        display_state: lcd_screen.DisplayState | None = None
        next_display_state: lcd_screen.DisplayState | None = None
        rotation_deadline = 0.0
        event_state: lcd_screen.DisplayState | None = None
        event_deadline: datetime | None = None

        current_offset = 0.0

        while current_offset < duration:
            now_dt = start_dt + timedelta(seconds=current_offset)

            if event_state is not None and event_deadline is not None:
                if now_dt >= event_deadline:
                    event_state = None
                    event_deadline = None
                    payload, expires_at = load_next_event(now_dt)
                    if payload is not None and expires_at is not None:
                        event_state = lcd_screen._prepare_display_state(
                            payload.line1, payload.line2, payload.scroll_ms
                        )
                        event_deadline = expires_at
                    else:
                        if state_order:
                            state_index = (state_index + 1) % len(state_order)
                        display_state = None
                        next_display_state = None
                        rotation_deadline = 0.0

            if event_state is None and event_deadline is None:
                payload, expires_at = load_next_event(now_dt)
                if payload is not None and expires_at is not None:
                    event_state = lcd_screen._prepare_display_state(
                        payload.line1, payload.line2, payload.scroll_ms
                    )
                    event_deadline = expires_at

            if event_state is None:
                if display_state is None or current_offset >= rotation_deadline:
                    high_payload, low_payload, high_available, low_available = load_payloads(now_dt)
                    refresh_state_order(high_available, low_available)

                    current_payload, clock_cycle = payload_for_state(
                        state_index,
                        now_dt=now_dt,
                        high_payload=high_payload,
                        low_payload=low_payload,
                        clock_cycle=clock_cycle,
                        state_order=state_order,
                    )
                    display_state = lcd_screen._prepare_display_state(
                        current_payload.line1,
                        current_payload.line2,
                        current_payload.scroll_ms,
                    )
                    rotation_deadline = current_offset + lcd_screen.ROTATION_SECONDS

                    next_index = (state_index + 1) % len(state_order)
                    next_payload, clock_cycle = payload_for_state(
                        next_index,
                        now_dt=now_dt,
                        high_payload=high_payload,
                        low_payload=low_payload,
                        clock_cycle=clock_cycle,
                        state_order=state_order,
                    )
                    next_display_state = lcd_screen._prepare_display_state(
                        next_payload.line1,
                        next_payload.line2,
                        next_payload.scroll_ms,
                    )

            if event_state is not None:
                label = "event"
                event_state, _ = lcd_screen._advance_display(
                    event_state,
                    writer,
                    label=label,
                    timestamp=now_dt,
                )
                frame = writer.pop_frame()
                if frame is not None:
                    yield PlannedFrame(
                        current_offset,
                        frame.timestamp,
                        frame.label,
                        frame.line1,
                        frame.line2,
                    )
                step = event_state.scroll_sec if event_state else lcd_screen.DEFAULT_FALLBACK_SCROLL_SEC
            else:
                label = state_order[state_index] if state_order else "clock"
                if display_state is None:
                    break
                display_state, _ = lcd_screen._advance_display(
                    display_state,
                    writer,
                    label=label,
                    timestamp=now_dt,
                )
                frame = writer.pop_frame()
                if frame is not None:
                    yield PlannedFrame(
                        current_offset,
                        frame.timestamp,
                        frame.label,
                        frame.line1,
                        frame.line2,
                    )
                step = display_state.scroll_sec or lcd_screen.DEFAULT_FALLBACK_SCROLL_SEC

            current_offset += step

            if event_state is None and current_offset >= rotation_deadline:
                if state_order:
                    state_index = (state_index + 1) % len(state_order)
                display_state = next_display_state
                high_payload, low_payload, _, _ = load_payloads(now_dt)
                next_index = (state_index + 1) % len(state_order) if state_order else 0
                next_payload, clock_cycle = payload_for_state(
                    next_index,
                    now_dt=now_dt,
                    high_payload=high_payload,
                    low_payload=low_payload,
                    clock_cycle=clock_cycle,
                    state_order=state_order,
                )
                next_display_state = lcd_screen._prepare_display_state(
                    next_payload.line1,
                    next_payload.line2,
                    next_payload.scroll_ms,
                )
                rotation_deadline = current_offset + lcd_screen.ROTATION_SECONDS

    def _event_lock_files(self, lock_dir: Path) -> list[Path]:
        return sorted(
            lock_dir.glob(lcd_screen.EVENT_LOCK_GLOB),
            key=lcd_screen._event_lock_sort_key,
        )

    def _now(self) -> datetime:
        return datetime.now(tz=timezone.utc)

    def _monotonic(self) -> float:
        return time.monotonic()

    def _sleep(self, seconds: float) -> None:
        time.sleep(seconds)
