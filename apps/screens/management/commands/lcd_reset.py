from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import models
from django.utils import timezone

from apps.core.notifications import NotificationManager
from apps.nodes.models import NetMessage, Node
from apps.screens.startup_notifications import (
    LCD_HIGH_LOCK_FILE,
    LCD_LOW_LOCK_FILE,
    LCD_RUNTIME_LOCK_FILE,
)


class Command(BaseCommand):
    help = "Restart the LCD service, clean lock files, and regenerate active NetMessage locks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--service-name",
            help=(
                "Base service name (defaults to the content of .locks/service.lck). "
                "The lcd unit is derived as lcd-<service>."
            ),
        )
        parser.add_argument(
            "--skip-restart",
            action="store_true",
            help="Skip restarting the lcd service before rebuilding lock files.",
        )

    def handle(self, *args, **options):
        base_dir = Path(getattr(settings, "BASE_DIR", Path.cwd()))
        lock_dir = base_dir / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)

        self._clean_lock_files(lock_dir)

        service_name = options.get("service_name") or self._read_service_name(base_dir)
        if not options.get("skip_restart"):
            self._restart_lcd_service(service_name)

        local = Node.get_local()
        if not local:
            self.stdout.write(
                self.style.WARNING("Local node not found; skipping NetMessage lock rebuild")
            )
            return

        active_messages = self._active_messages(local)
        if not active_messages:
            self.stdout.write(self.style.WARNING("No active NetMessages to rebuild"))
            return

        manager = NotificationManager(lock_dir=lock_dir)
        for message in active_messages:
            channel_type, channel_num = NetMessage.normalize_lcd_channel(
                message.lcd_channel_type, message.lcd_channel_num
            )
            manager.send(
                message.subject,
                message.body,
                expires_at=message.expires_at,
                channel_type=channel_type,
                channel_num=channel_num,
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Rebuilt {len(active_messages)} NetMessage lock file(s) for the LCD"
            )
        )

    def _clean_lock_files(self, lock_dir: Path) -> None:
        for name in (LCD_HIGH_LOCK_FILE, LCD_LOW_LOCK_FILE, LCD_RUNTIME_LOCK_FILE):
            path = lock_dir / name
            try:
                path.unlink(missing_ok=True)
            except Exception:
                # Failure to remove a stale lock should not block the reset process.
                continue

    def _read_service_name(self, base_dir: Path) -> str | None:
        service_file = base_dir / ".locks" / "service.lck"
        try:
            return service_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _restart_lcd_service(self, service_name: str | None) -> None:
        systemctl = shutil.which("systemctl")
        if not systemctl:
            self.stdout.write(self.style.WARNING("systemctl not available; skipping restart"))
            return

        if not service_name:
            raise CommandError("Service name is required to restart the lcd updater")

        lcd_unit = f"lcd-{service_name}"
        result = subprocess.run(
            [systemctl, "restart", lcd_unit], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            error_output = result.stderr.strip() or result.stdout.strip()
            raise CommandError(f"Failed to restart {lcd_unit}: {error_output}")

        self.stdout.write(self.style.SUCCESS(f"Restarted {lcd_unit}"))

    def _active_messages(self, local: Node) -> list[NetMessage]:
        now = timezone.now()
        qs = NetMessage.objects.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).order_by("created")
        return [msg for msg in qs if self._targets_local(msg, local)]

    def _targets_local(self, message: NetMessage, local: Node) -> bool:
        if message.filter_node_id and message.filter_node_id != local.pk:
            return False

        if message.filter_node_feature_id:
            if not local.features.filter(pk=message.filter_node_feature_id).exists():
                return False

        if message.filter_node_role_id and message.filter_node_role_id != local.role_id:
            return False

        if message.filter_current_relation and (
            local.current_relation != message.filter_current_relation
        ):
            return False

        if message.filter_installed_version and (
            (local.installed_version or "") != message.filter_installed_version
        ):
            return False

        if message.filter_installed_revision and (
            (local.installed_revision or "") != message.filter_installed_revision
        ):
            return False

        return True
