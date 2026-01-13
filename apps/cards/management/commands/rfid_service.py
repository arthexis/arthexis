from __future__ import annotations

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

    def handle(self, *args, **options):
        host = options.get("host")
        port = options.get("port")
        self.stdout.write(
            self.style.SUCCESS(f"Starting RFID service on {host}:{port}")
        )
        run_service(host=host, port=port)
