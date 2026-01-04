from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LcdMessage,
    read_lcd_lock_file,
    render_lcd_lock_file,
)
from apps.sigils.sigil_resolver import resolve_sigils


class Command(BaseCommand):
    """Update the LCD lock file or restart the LCD updater service."""

    help = "Write subject/body to the lcd lock file, delete it, or restart the updater"

    def add_arguments(self, parser):
        parser.add_argument("--subject", help="First LCD line (max 64 chars)")
        parser.add_argument("--body", help="Second LCD line (max 64 chars)")
        parser.add_argument(
            "--sticky",
            action="store_true",
            help="Write to the high-priority LCD lock instead of the low lock",
        )
        parser.add_argument(
            "--delete",
            action="store_true",
            help="Delete the lcd lock file instead of writing to it",
        )
        parser.add_argument(
            "--restart",
            action="store_true",
            help="Restart the lcd updater service after modifying the lock file",
        )
        parser.add_argument(
            "--no-resolve",
            dest="resolve_sigils",
            action="store_false",
            default=True,
            help="Disable resolving [SIGILS] in subject/body before writing the lock file",
        )
        parser.add_argument(
            "--service",
            dest="service_name",
            help=(
                "Base service name (defaults to the content of .locks/service.lck). "
                "The lcd unit is derived as lcd-<service>."
            ),
        )

    def handle(self, *args, **options):
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        target_name = LCD_HIGH_LOCK_FILE if options.get("sticky") else LCD_LOW_LOCK_FILE
        lock_file = lock_dir / target_name

        if options["delete"]:
            self._delete_lock_file(lock_file)
        else:
            self._write_lock_file(lock_dir, lock_file, options)

        if options["restart"]:
            self._restart_service(base_dir=base_dir, service_name=options.get("service_name"))

    # ------------------------------------------------------------------
    def _delete_lock_file(self, lock_file: Path) -> None:
        if lock_file.exists():
            lock_file.unlink()
            self.stdout.write(self.style.SUCCESS(f"Deleted {lock_file}"))
        else:
            self.stdout.write(self.style.WARNING(f"Lock file not found: {lock_file}"))

    def _write_lock_file(self, lock_dir: Path, lock_file: Path, options: dict) -> None:
        lock_dir.mkdir(parents=True, exist_ok=True)
        existing = read_lcd_lock_file(lock_file) or self._default_lock_payload()

        subject = (
            options.get("subject")
            if options.get("subject") is not None
            else existing.subject
        )
        body = options.get("body") if options.get("body") is not None else existing.body
        expires_at = existing.expires_at

        if options.get("resolve_sigils"):
            subject = resolve_sigils(subject)
            body = resolve_sigils(body)

        payload = render_lcd_lock_file(subject=subject, body=body, expires_at=expires_at)
        lock_file.write_text(payload, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Updated {lock_file}"))

    def _default_lock_payload(self) -> LcdMessage:
        return LcdMessage(subject="", body="")

    def _restart_service(self, *, base_dir: Path, service_name: str | None) -> None:
        resolved_service = service_name or self._read_service_name(base_dir)
        if not resolved_service:
            raise CommandError("Service name is required to restart the lcd updater")

        lcd_unit = f"lcd-{resolved_service}"
        try:
            result = subprocess.run(
                ["systemctl", "restart", lcd_unit],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            raise CommandError("systemctl not available; cannot restart lcd service")

        if result.returncode != 0:
            error_output = (result.stderr or result.stdout or "Unknown error").strip()
            raise CommandError(f"Failed to restart {lcd_unit}: {error_output}")

        self.stdout.write(self.style.SUCCESS(f"Restarted {lcd_unit}"))

    def _read_service_name(self, base_dir: Path) -> str | None:
        service_file = base_dir / ".locks" / "service.lck"
        try:
            return service_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
