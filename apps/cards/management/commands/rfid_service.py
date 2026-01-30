from __future__ import annotations

import logging

from django.core.management.base import BaseCommand

from apps.cards.rfid_service import run_service, service_endpoint


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
            action="store_true",
            help="Enable debug logging for interactive troubleshooting",
        )

    def handle(self, *args, **options):
        host = options.get("host")
        port = options.get("port")
        if options.get("debug"):
            rfid_logger = logging.getLogger("apps.cards.rfid_service")
            rfid_logger.setLevel(logging.DEBUG)
            if not any(
                getattr(handler, "name", "") == "rfid-debug"
                for handler in rfid_logger.handlers
            ):
                handler = logging.StreamHandler()
                handler.name = "rfid-debug"
                handler.setLevel(logging.DEBUG)
                handler.setFormatter(
                    logging.Formatter(
                        "%(asctime)s %(levelname)s %(name)s: %(message)s"
                    )
                )
                rfid_logger.addHandler(handler)
        self.stdout.write(
            self.style.SUCCESS(f"Starting RFID service on {host}:{port}")
        )
        run_service(host=host, port=port)
