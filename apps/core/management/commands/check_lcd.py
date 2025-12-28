from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.notifications import notify
from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE


class Command(BaseCommand):
    """Send a test message to the LCD and validate lock-file handling."""

    help = "Send a test message to the LCD and validate lock-file handling"

    def add_arguments(self, parser) -> None:
        parser.add_argument("message", help="Text to send to the LCD display")
        parser.add_argument(
            "--timeout",
            type=float,
            default=10.0,
            help="Seconds to wait for the LCD daemon to process the message",
        )
        parser.add_argument(
            "--poll-interval",
            type=float,
            default=0.2,
            help="Seconds between lock-file checks",
        )

    def handle(self, *args, **options):
        message: str = options["message"]
        timeout: float = options["timeout"]
        poll_interval: float = options["poll_interval"]

        base_dir = Path(settings.BASE_DIR)
        lock_file = base_dir / ".locks" / LCD_LOW_LOCK_FILE
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        self.stdout.write(f"Sending test message to LCD: {message}")
        self._clear_existing_lock(lock_file)

        notify(subject=message)

        if not self._wait_for_lock_write(lock_file, message, timeout, poll_interval):
            raise CommandError("Lock file was not written by notification helper")

        self.stdout.write(self.style.SUCCESS("Lock file written with test message"))

        if self._wait_for_lock_persist(lock_file, message, timeout, poll_interval):
            self.stdout.write(
                self.style.SUCCESS("LCD daemon kept the lock file message sticky")
            )
            return

        raise CommandError(
            "LCD daemon did not keep the lock file message sticky"
        )

    def _clear_existing_lock(self, lock_file: Path) -> None:
        try:
            lock_file.unlink()
        except FileNotFoundError:
            return
        except OSError:
            # If we cannot remove the stale file, continue so the test can still
            # attempt to verify the current lock state.
            pass

    def _wait_for_condition(
        self, predicate, timeout: float, poll_interval: float
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(poll_interval)
        return predicate()

    def _wait_for_lock_write(
        self, lock_file: Path, message: str, timeout: float, poll_interval: float
    ) -> bool:
        expected = message[:64]

        def _written() -> bool:
            if not lock_file.exists():
                return False
            try:
                raw = lock_file.read_text(encoding="utf-8")
            except OSError:
                return False
            first_line = raw.splitlines()[0] if raw else ""
            return first_line.strip() == expected.strip()

        return self._wait_for_condition(_written, timeout, poll_interval)

    def _wait_for_lock_persist(
        self, lock_file: Path, message: str, timeout: float, poll_interval: float
    ) -> bool:
        expected = message[:64].strip()

        def _matches() -> bool:
            if not lock_file.exists():
                return False
            try:
                raw = lock_file.read_text(encoding="utf-8")
            except OSError:
                return False
            first_line = raw.splitlines()[0] if raw else ""
            return first_line.strip() == expected

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not _matches():
                return False
            time.sleep(poll_interval)
        return _matches()
