from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.skills.package_services import (
    export_codex_skill_package,
    import_codex_skill_package,
    scan_codex_skills_root,
)


class Command(BaseCommand):
    help = "Scan, export, or import portable Codex skill packages."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["scan", "export", "import"])
        parser.add_argument("--source", help="Codex skills root for scan.")
        parser.add_argument("--output", help="ZIP package path for export.")
        parser.add_argument("--package", help="ZIP package path for import.")
        parser.add_argument("--slug", action="append", dest="slugs", default=[])
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--include-excluded",
            action="store_true",
            help="Export package records even when they are excluded by default.",
        )

    def handle(self, *args, **options):
        action = options["action"]
        if action == "scan":
            source = options.get("source")
            if not source:
                raise CommandError("--source is required for scan")
            summary = scan_codex_skills_root(Path(source), dry_run=options["dry_run"])
        elif action == "export":
            output = options.get("output")
            if not output:
                raise CommandError("--output is required for export")
            summary = export_codex_skill_package(
                Path(output),
                skill_slugs=options["slugs"] or None,
                portable_only=not options["include_excluded"],
            )
        else:
            package = options.get("package")
            if not package:
                raise CommandError("--package is required for import")
            summary = import_codex_skill_package(
                Path(package),
                dry_run=options["dry_run"],
            )
        self.stdout.write(json.dumps(summary, indent=2, sort_keys=True))
