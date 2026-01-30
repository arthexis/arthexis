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
            "-v",
            "--verbose",
            action="store_true",
            help="Enable verbose logging for interactive troubleshooting",
        )

    def handle(self, *args, **options):
        host = options.get("host")
        port = options.get("port")
        if options.get("verbose"):
            logging.basicConfig(
                level=logging.DEBUG,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
            logging.getLogger("apps.cards.rfid_service").setLevel(logging.DEBUG)
        self.stdout.write(
            self.style.SUCCESS(f"Starting RFID service on {host}:{port}")
        )
        run_service(host=host, port=port)
