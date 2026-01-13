from __future__ import annotations

import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.core.notifications import NotificationManager
from apps.screens.startup_notifications import render_lcd_lock_file


class Command(BaseCommand):
    """Send a test message to the LCD and validate lock-file handling."""

    help = "Send a test message to the LCD and validate lock-file handling"

    def add_arguments(self, parser) -> None:
        parser.add_argument("subject", help="Text to send to the LCD display")
        parser.add_argument("--body", default="", help="Second line of the LCD message")
        parser.add_argument(
            "--expires-at",
            default=None,
            help="Optional expiration timestamp written to the lock file",
        )
        parser.add_argument(
            "--sticky",
            action="store_true",
            help="Write to the sticky (high-priority) lock file",
        )
        parser.add_argument(
            "--channel-type",
            default=None,
            help="LCD channel type to target (e.g. low, high, clock, uptime, custom)",
        )
        parser.add_argument(
            "--channel-num",
            default=None,
            help="LCD channel number to target when applicable",
        )
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
        subject: str = options["subject"]
        body: str = options["body"]
        expires_at = options["expires_at"]
        sticky: bool = options["sticky"]
        channel_type = options["channel_type"]
        channel_num = options["channel_num"]
        timeout: float = options["timeout"]
        poll_interval: float = options["poll_interval"]

        base_dir = Path(settings.BASE_DIR)
        manager = NotificationManager(lock_dir=base_dir / ".locks")
        lock_file = manager.get_target_lock_file(
            channel_type=channel_type,
            channel_num=channel_num,
            sticky=sticky,
        )
        lock_file.parent.mkdir(parents=True, exist_ok=True)

        expected_payload = render_lcd_lock_file(
            subject=subject,
            body=body,
            expires_at=expires_at,
        )

        self.stdout.write(
            f"Sending test message to LCD: subject='{subject}' body='{body}'"
        )
        self.stdout.write(f"Target lock file: {lock_file}")
        self._clear_existing_lock(lock_file)

        manager.send(
            subject=subject,
            body=body,
            sticky=sticky,
            expires_at=expires_at,
            channel_type=channel_type,
            channel_num=channel_num,
        )

        if not self._wait_for_lock_write(
            lock_file, expected_payload, timeout, poll_interval
        ):
            raise CommandError("Lock file was not written by notification helper")

        self.stdout.write(self.style.SUCCESS("Lock file written with test message"))

        if self._wait_for_lock_persist(
            lock_file, expected_payload, timeout, poll_interval
        ):
            self.stdout.write(
                self.style.SUCCESS("LCD daemon kept the lock file message sticky")
            )
            return

        raise CommandError(
            "LCD daemon did not keep the lock file message sticky",
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

    def _read_lock_payload_matches(
        self, lock_file: Path, expected_payload: str
    ) -> bool:
        if not lock_file.exists():
            return False
        try:
            raw = lock_file.read_text(encoding="utf-8")
        except OSError:
            return False
        return raw == expected_payload

    def _wait_for_lock_write(
        self, lock_file: Path, expected_payload: str, timeout: float, poll_interval: float
    ) -> bool:
        return self._wait_for_condition(
            lambda: self._read_lock_payload_matches(lock_file, expected_payload),
            timeout,
            poll_interval,
        )

    def _wait_for_lock_persist(
        self, lock_file: Path, expected_payload: str, timeout: float, poll_interval: float
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._read_lock_payload_matches(lock_file, expected_payload):
                return False
            time.sleep(poll_interval)
        return self._read_lock_payload_matches(lock_file, expected_payload)
