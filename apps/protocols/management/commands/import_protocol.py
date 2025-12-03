from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.protocols.services import ProtocolSpecError, import_protocol_spec


class Command(BaseCommand):
    help = "Import a protocol definition from JSON into the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug", help="Protocol slug (e.g., ocpp16)")
        parser.add_argument(
            "--path",
            help="Optional path to a JSON spec. Defaults to bundled spec file when omitted.",
        )

    def handle(self, *args, **options):
        slug = options["slug"]
        path_opt = options.get("path")
        path = Path(path_opt) if path_opt else None
        try:
            protocol = import_protocol_spec(slug, path)
        except ProtocolSpecError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(f"Imported protocol {protocol.slug}"))
