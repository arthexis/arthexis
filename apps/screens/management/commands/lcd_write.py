from __future__ import annotations

import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.screens.startup_notifications import (
    LCD_LOCK_FILE,
    LCD_STATE_DISABLED,
    LCD_STATE_ENABLED,
    LcdLockFile,
    ensure_lcd_lock_file,
    read_lcd_lock_file,
    render_lcd_lock_file,
)
from apps.sigils.sigil_resolver import resolve_sigils


class Command(BaseCommand):
    """Update the LCD lock file or restart the LCD updater service."""

    help = (
        "Write subject/body/state/flags to the lcd lock file, delete it, or restart"
        " the lcd updater service."
    )

    def add_arguments(self, parser):
        parser.add_argument("--state", choices=[LCD_STATE_ENABLED, LCD_STATE_DISABLED])
        parser.add_argument("--subject", help="First LCD line (max 64 chars)")
        parser.add_argument("--body", help="Second LCD line (max 64 chars)")
        parser.add_argument(
            "--flag",
            action="append",
            dest="flags",
            help="Additional flags such as 'net-message' or 'scroll_ms=1500'",
        )
        parser.add_argument(
            "--clear-flags",
            action="store_true",
            help="Remove existing flags before applying new ones",
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
            help="Disable resolving [SIGILS] in subject/body/flags before writing the lock file",
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
        lock_file = lock_dir / LCD_LOCK_FILE

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
        existing = read_lcd_lock_file(lock_file) or self._default_lock_payload(lock_dir)

        state = options.get("state") or existing.state
        subject = options.get("subject") if options.get("subject") is not None else existing.subject
        body = options.get("body") if options.get("body") is not None else existing.body

        if options.get("clear_flags"):
            flags: list[str] = []
        else:
            flags = list(existing.flags)

        for flag in options.get("flags") or []:
            if flag and flag not in flags:
                flags.append(flag)

        if options.get("resolve_sigils"):
            subject = resolve_sigils(subject)
            body = resolve_sigils(body)
            flags = [resolve_sigils(flag) for flag in flags]

        payload = render_lcd_lock_file(
            state=state,
            subject=subject,
            body=body,
            flags=flags,
        )
        lock_file.write_text(payload, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Updated {lock_file}"))

    def _default_lock_payload(self, lock_dir: Path) -> LcdLockFile:
        ensure_lcd_lock_file(lock_dir)
        payload = read_lcd_lock_file(lock_dir / LCD_LOCK_FILE)
        if payload:
            return payload
        return LcdLockFile(
            state=LCD_STATE_ENABLED, subject="", body="", flags=()
        )

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
