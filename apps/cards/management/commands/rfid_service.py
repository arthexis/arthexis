from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.cards.rfid_service import (
    rfid_service_enabled,
    run_service,
    service_endpoint,
)
from apps.loggers.handlers import RFIDFileHandler


class Command(BaseCommand):
    help = "Run the RFID scanner UDP service"

    def add_arguments(self, parser):
        endpoint = service_endpoint()
        parser.add_argument(
            "--host",
            default=endpoint.host,
            help="Host interface to bind the RFID service",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=endpoint.port,
            help="UDP port to bind the RFID service",
        )
        parser.add_argument(
            "--debug",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="Enable or disable debug logging for interactive troubleshooting",
        )

    def handle(self, *args, **options):
        host = options.get("host")
        port = options.get("port")
        debug_enabled = options.get("debug", False)
        if debug_enabled:
            self._prepare_debug_service()
        rfid_logger = logging.getLogger("apps.cards.rfid_service")
        rfid_logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)
        self._configure_rfid_handler(rfid_logger, debug_enabled)
        self.stdout.write(
            self.style.SUCCESS(f"Starting RFID service on {host}:{port}")
        )
        run_service(host=host, port=port)

    @staticmethod
    def _configure_rfid_handler(
        logger: logging.Logger, debug_enabled: bool
    ) -> None:
        level = logging.DEBUG if debug_enabled else logging.INFO
        handler_updated = False
        for handler in logger.handlers:
            if isinstance(handler, RFIDFileHandler):
                handler.setLevel(level)
                handler_updated = True
        if handler_updated:
            return
        handler = RFIDFileHandler(
            filename="rfid.log",
            when="midnight",
            backupCount=3,
            encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)

    def _prepare_debug_service(self) -> None:
        base_dir = Path(settings.BASE_DIR)
        lock_dir = base_dir / ".locks"
        feature_enabled = rfid_service_enabled(lock_dir)
        service_name = self._resolve_service_name(base_dir)

        if not service_name:
            if feature_enabled:
                self.stdout.write(
                    self.style.WARNING(
                        "RFID service feature is enabled, but .locks/service.lck is missing; "
                        "unable to stop the systemd service before debug start."
                    )
                )
            return

        unit_name = f"rfid-{service_name}.service"
        active = self._systemd_is_active(unit_name)
        if active is True:
            self._stop_systemd_unit(unit_name)
            return

        if feature_enabled:
            self.stdout.write(
                self.style.WARNING(
                    f"RFID service feature is enabled, but {unit_name} is not active; "
                    "starting a debug instance."
                )
            )

    def _resolve_service_name(self, base_dir: Path) -> str | None:
        service_file = base_dir / ".locks" / "service.lck"
        if service_file.exists():
            return service_file.read_text(encoding="utf-8").strip() or None
        return None

    def _systemd_is_active(self, unit_name: str) -> bool | None:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    f"systemctl not available; cannot verify {unit_name} status before debug start."
                )
            )
            return None
        return result.returncode == 0 and result.stdout.strip() == "active"

    def _stop_systemd_unit(self, unit_name: str) -> None:
        self.stdout.write(f"Stopping {unit_name} to start debug service...")
        try:
            result = subprocess.run(
                ["systemctl", "stop", unit_name],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            self.stdout.write(
                self.style.WARNING(
                    f"systemctl not available; cannot stop {unit_name} before debug start."
                )
            )
            return
        if result.returncode != 0:
            error_output = (result.stderr or "").strip()
            self.stdout.write(
                self.style.WARNING(
                    f"Failed to stop {unit_name} before debug start: {error_output}"
                )
            )
            return
        self.stdout.write(self.style.SUCCESS(f"Stopped {unit_name}"))
