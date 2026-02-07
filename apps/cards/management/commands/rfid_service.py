from __future__ import annotations

import argparse
import logging

from django.core.management.base import BaseCommand

from apps.cards.rfid_service import run_service, service_endpoint
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
