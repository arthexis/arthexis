from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.screens.lcd import (
    LCDController,
    LCDTimings,
    LCDUnavailableError,
    prepare_lcd_controller,
)


class Command(BaseCommand):
    help = "Calibrate LCD timing settings and save them to .locks/lcd-timings"

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--service",
            dest="service_name",
            help="Base service name (defaults to the content of .locks/service.lck).",
        )
        parser.add_argument(
            "--lock-file",
            dest="lock_file",
            help="Override the lock file path (defaults to .locks/lcd-timings).",
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help="Restart the LCD service after calibration.",
        )

    def handle(self, *args, **options) -> None:
        base_dir = Path(settings.BASE_DIR)
        lock_file = self._resolve_lock_file(base_dir, options.get("lock_file"))
        service_name = self._resolve_service_name(base_dir, options.get("service_name"))

        if service_name:
            self._stop_lcd_service(service_name)
        else:
            self.stdout.write(
                self.style.WARNING(
                    "No service name found; skipping lcd service stop. "
                    "Provide --service or ensure .locks/service.lck exists."
                )
            )

        timings = LCDTimings.from_configuration(base_dir=base_dir)
        lcd = self._initialize_lcd(base_dir)
        if lcd is not None:
            self._show_preview(lcd, timings, label="Current timing preview")

        timings = self._prompt_for_timings(timings)
        if lcd is not None:
            lcd.timings = timings
            self._show_preview(lcd, timings, label="Updated timing preview")

        if self._confirm_save(lock_file):
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text(timings.to_lock_file(), encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Saved LCD timings to {lock_file}"))
        else:
            self.stdout.write(self.style.WARNING("Calibration cancelled; no changes saved."))

        if options.get("restart"):
            if not service_name:
                raise CommandError("Service name is required to restart the lcd service")
            self._restart_lcd_service(service_name)

    def _resolve_lock_file(self, base_dir: Path, override: str | None) -> Path:
        if override:
            return Path(override)
        return base_dir / ".locks" / LCDTimings.lock_file_name

    def _resolve_service_name(self, base_dir: Path, override: str | None) -> str | None:
        if override:
            return override
        service_file = base_dir / ".locks" / "service.lck"
        if service_file.exists():
            return service_file.read_text(encoding="utf-8").strip() or None
        return None

    def _stop_lcd_service(self, service_name: str) -> None:
        self._run_systemctl("stop", service_name)

    def _restart_lcd_service(self, service_name: str) -> None:
        self._run_systemctl("restart", service_name)

    def _run_systemctl(self, action: str, service_name: str) -> None:
        lcd_unit = f"lcd-{service_name}"
        action_ing = {"stop": "Stopping", "restart": "Restarting"}.get(
            action, f"{action.capitalize()}ing"
        )
        action_ed = {"stop": "Stopped", "restart": "Restarted"}.get(
            action, f"{action.capitalize()}ed"
        )
        self.stdout.write(f"{action_ing} {lcd_unit}...")
        try:
            result = subprocess.run(
                ["systemctl", action, lcd_unit],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CommandError(
                f"systemctl not available; cannot {action} lcd service"
            ) from exc

        if result.returncode != 0:
            error_output = (result.stderr or "").strip()
            raise CommandError(f"Failed to {action} {lcd_unit}: {error_output}")

        self.stdout.write(self.style.SUCCESS(f"{action_ed} {lcd_unit}"))

    def _initialize_lcd(self, base_dir: Path) -> LCDController | None:
        try:
            return prepare_lcd_controller(base_dir=base_dir, preference="pcf8574")
        except LCDUnavailableError as exc:
            self.stdout.write(
                self.style.WARNING(f"LCD unavailable ({exc}); skipping hardware preview")
            )
        except Exception as exc:
            self.stdout.write(
                self.style.WARNING(f"LCD init failed ({exc}); skipping hardware preview")
            )
        return None

    def _show_preview(self, lcd: LCDController, timings: LCDTimings, *, label: str) -> None:
        self.stdout.write(label)
        lcd.timings = timings
        lcd.clear()
        lcd.write(0, 0, "LCD CALIBRATE")
        lcd.write(0, 1, "1234567890ABCDEF")

    def _prompt_for_timings(self, timings: LCDTimings) -> LCDTimings:
        self.stdout.write(
            "Enter timing values in seconds. Press Enter to keep the current value."
        )
        prompts = [
            (key, key.replace("_", " ").capitalize())
            for key in LCDTimings._lock_fields
        ]

        for key, label in prompts:
            current = getattr(timings, key)
            while True:
                response = input(f"{label} [{current:.6f}]: ").strip()
                if not response:
                    break
                try:
                    value = float(response)
                except ValueError:
                    self.stdout.write(self.style.WARNING("Please enter a valid number."))
                    continue
                setattr(timings, key, value)
                break

        self.stdout.write("Updated timings:")
        for key in LCDTimings._lock_fields:
            value = getattr(timings, key)
            self.stdout.write(f"- {key}: {value:.6f}s")
        return timings

    def _confirm_save(self, lock_file: Path) -> bool:
        response = input(f"Save timings to {lock_file}? [y/N]: ").strip().lower()
        return response in {"y", "yes"}
