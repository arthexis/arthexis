from __future__ import annotations

import random
import string
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.core.notifications import notify
from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE
from apps.screens.lcd import LCDUnavailableError, prepare_lcd_controller


class Command(BaseCommand):
    """Validate LCD service setup and display a test message."""

    help = "Validate LCD service setup and display a test message"

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        lock_file = base_dir / ".locks" / LCD_LOW_LOCK_FILE
        service_file = base_dir / ".locks" / "service.lck"

        self.stdout.write("LCD diagnostic report:")

        # Lock file check -------------------------------------------------
        if lock_file.exists():
            content = lock_file.read_text(encoding="utf-8").strip()
            if content:
                self.stdout.write(
                    self.style.SUCCESS("Lock file exists and contains data")
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Lock file is empty; startup trigger may not have executed"
                    )
                )
        else:
            self.stdout.write(
                self.style.ERROR("Lock file missing; LCD service may not be running")
            )

        # Service status check -------------------------------------------
        if service_file.exists():
            service_name = service_file.read_text(encoding="utf-8").strip()
            lcd_service = f"lcd-{service_name}"
            try:
                result = subprocess.run(
                    ["systemctl", "is-active", lcd_service],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0 and result.stdout.strip() == "active":
                    self.stdout.write(
                        self.style.SUCCESS(f"Service {lcd_service} is active")
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(f"Service {lcd_service} is not active")
                    )
            except FileNotFoundError:
                self.stdout.write(
                    self.style.WARNING("systemctl not available; cannot verify service")
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "Service lock file missing; cannot determine LCD service"
                )
            )

        # I2C bus check --------------------------------------------------
        try:
            prepare_lcd_controller(base_dir=base_dir)
            self.stdout.write(
                self.style.SUCCESS("I2C communication with LCD succeeded")
            )
        except LCDUnavailableError:
            self.stdout.write(
                self.style.ERROR("LCDUnavailableError: cannot access I2C bus")
            )
        except Exception as exc:  # pragma: no cover - unexpected failures
            self.stdout.write(
                self.style.ERROR(f"Unexpected error during LCD init: {exc}")
            )
            if isinstance(exc, FileNotFoundError) and "/dev/i2c-1" in str(exc):
                self.stdout.write(
                    self.style.WARNING(
                        "Hint: enable the I2C interface or check that the LCD is wired correctly. "
                        "On Raspberry Pi, run 'sudo raspi-config' then enable I2C under Interfacing Options"
                    )
                )

        # Random string display -----------------------------------------
        random_text = "".join(
            random.choices(string.ascii_uppercase + string.digits, k=6)
        )
        notify(subject=random_text)
        self.stdout.write(f"Displayed random string: {random_text}")
        answer = input("Did the LCD display this string? [y/N]: ").strip().lower()
        if answer.startswith("y"):
            self.stdout.write(self.style.SUCCESS("LCD display confirmed"))
        else:
            self.stdout.write(self.style.WARNING("LCD display not confirmed by user"))
