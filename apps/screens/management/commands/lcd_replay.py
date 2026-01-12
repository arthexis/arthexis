from __future__ import annotations

import select
import signal
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import psutil
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.screens.history import (
    HistoryEntry,
    load_history_entries,
    select_entry_for_timestamp,
)
from apps.screens.lcd import LCDUnavailableError, prepare_lcd_controller
from apps.screens.lcd_screen import LCDFrameWriter

try:
    import termios
    import tty
except ImportError:
    termios = None
    tty = None


class LCDHistoryReplayer:
    def __init__(
        self,
        entries: list[HistoryEntry],
        writer: LCDFrameWriter,
        *,
        start_index: int = 0,
        stdout=None,
        key_reader: Callable[[float], str | None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.entries = entries
        self.writer = writer
        self.stdout = stdout or sys.stdout
        self.key_reader = key_reader or self._read_key
        self.sleep = sleep
        self.index = max(0, min(start_index, len(entries) - 1))
        self.playing = True
        self.poll_interval = 0.1
        self.next_deadline = time.monotonic()

    def run(self) -> None:
        if not self.entries:
            return

        self.stdout.write(
            "Controls: [s]tart/[p]ause/[x]stop, [r]ewind, [f]ast-forward, [q]uit"
        )
        self._display_current()
        self._schedule_next()

        with self._keyboard_mode():
            while True:
                key = self.key_reader(self.poll_interval)
                if key and self._handle_key(key):
                    break

                if self.playing and time.monotonic() >= self.next_deadline:
                    self._step_forward()

    # ------------------------------------------------------------------
    def _handle_key(self, key: str) -> bool:
        key = key.lower()
        if key in {"q", "\u0003"}:  # quit or Ctrl+C
            return True

        if key == "s":
            self.playing = True
            self._schedule_next()
        elif key == "p":
            self.playing = False
        elif key == "x":
            self.playing = False
            self.index = 0
            self._display_current()
        elif key == "r":
            self.index = max(0, self.index - 1)
            self._display_current()
            self._schedule_next()
        elif key == "f":
            self.index = min(len(self.entries) - 1, self.index + 1)
            self._display_current()
            self._schedule_next()

        return False

    def _display_current(self) -> None:
        entry = self.entries[self.index]
        self.writer.write(
            entry.line1,
            entry.line2,
            label=entry.label,
            timestamp=entry.timestamp,
        )
        self.stdout.write(
            f"[{entry.timestamp.isoformat()}] {entry.line1} | {entry.line2}"
        )

    def _step_forward(self) -> None:
        if self.index < len(self.entries) - 1:
            self.index += 1
        self._display_current()
        self._schedule_next()

    def _schedule_next(self) -> None:
        delay = self._next_delay()
        self.next_deadline = time.monotonic() + delay

    def _next_delay(self) -> float:
        if self.index >= len(self.entries) - 1:
            return 1.0

        current = self.entries[self.index]
        upcoming = self.entries[self.index + 1]
        gap = (upcoming.timestamp - current.timestamp).total_seconds()
        return max(0.2, min(5.0, gap))

    def _read_key(self, timeout: float) -> str | None:
        if not sys.stdin.isatty():
            self.sleep(timeout)
            return None

        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        if ready:
            return sys.stdin.read(1)
        return None

    @contextmanager
    def _keyboard_mode(self):
        if not sys.stdin.isatty():
            yield
            return

        if termios is None or tty is None:
            yield
            return

        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            yield
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


class Command(BaseCommand):
    help = "Replay historical LCD frames from work/lcd-history-x.txt files"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--at",
            dest="timestamp",
            help="ISO timestamp (UTC) or date (YYYY-MM-DD) to replay",
        )
        parser.add_argument("--days", type=int, default=0, help="Days in the past")
        parser.add_argument("--hours", type=int, default=0, help="Hours in the past")
        parser.add_argument(
            "--minutes", type=int, default=0, help="Minutes in the past"
        )
        parser.add_argument(
            "--service",
            dest="service_name",
            help="Service name used to locate and interrupt the running LCD updater",
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        target_time = self._resolve_target_time(options)

        entries = load_history_entries(base_dir)
        if not entries:
            raise CommandError("No LCD history entries available to replay")

        entry = select_entry_for_timestamp(entries, target_time)
        if entry is None:
            raise CommandError("No history entry found for the requested time")

        interrupted = self._interrupt_lcd_service(options.get("service_name"))
        if interrupted:
            self.stdout.write(self.style.WARNING("Interrupted running lcd service"))

        writer = self._build_writer(base_dir)
        start_index = entries.index(entry)
        replayer = self._build_replayer(entries, writer, start_index=start_index)
        replayer.run()

    # ------------------------------------------------------------------
    def _resolve_target_time(self, options: dict) -> datetime:
        provided_timestamp = options.get("timestamp")
        delta = timedelta(
            days=options.get("days") or 0,
            hours=options.get("hours") or 0,
            minutes=options.get("minutes") or 0,
        )

        if provided_timestamp:
            try:
                parsed = datetime.fromisoformat(provided_timestamp)
            except ValueError as exc:
                raise CommandError("Invalid timestamp format; use ISO-8601") from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)

            parsed = parsed.astimezone(timezone.utc)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
            if parsed < cutoff:
                raise CommandError("LCD history is limited to the last 72 hours")
            return parsed

        if delta <= timedelta(0):
            raise CommandError("Provide --at or a positive time delta to replay")

        now = datetime.now(timezone.utc)
        if delta > timedelta(hours=72):
            raise CommandError("LCD history is limited to the last 72 hours")

        return now - delta

    def _interrupt_lcd_service(self, service_name: str | None) -> bool:
        interrupted = False
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                cmdline = process.info.get("cmdline") or []
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            joined = " ".join(cmdline)
            if "apps.screens.lcd_screen" in joined or "lcd-screen" in joined:
                if service_name and service_name not in joined:
                    continue
                try:
                    process.send_signal(signal.SIGINT)
                    interrupted = True
                except Exception:
                    continue

        return interrupted

    def _build_writer(self, base_dir: Path) -> LCDFrameWriter:
        try:
            lcd = prepare_lcd_controller(base_dir=base_dir)
            return LCDFrameWriter(lcd)
        except LCDUnavailableError as exc:
            work_dir = base_dir / "work"
            work_dir.mkdir(parents=True, exist_ok=True)
            fallback = work_dir / "lcd-replay.txt"
            self.stdout.write(
                self.style.WARNING(
                    f"LCD unavailable ({exc}); writing replay frames to {fallback}"
                )
            )
            return LCDFrameWriter(None, work_file=fallback)

    def _build_replayer(
        self, entries: list[HistoryEntry], writer: LCDFrameWriter, *, start_index: int
    ) -> LCDHistoryReplayer:
        return LCDHistoryReplayer(
            entries,
            writer,
            start_index=start_index,
            stdout=self.stdout,
        )
