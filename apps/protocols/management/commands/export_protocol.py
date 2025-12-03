from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.protocols.services import ProtocolSpecError, export_protocol_spec


class Command(BaseCommand):
    help = "Export a protocol definition from the database to JSON."

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug", help="Protocol slug (e.g., ocpp16)")
        parser.add_argument(
            "--output",
            required=True,
            help="Destination JSON path for the exported protocol spec.",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        output = Path(options["output"])
        try:
            export_protocol_spec(slug, output)
        except ProtocolSpecError as exc:
            raise CommandError(str(exc))
        except Exception as exc:  # pragma: no cover - filesystem errors
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Exported {slug} to {output}"))
