from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.skills.workgroup import (
    ensure_workgroup_file,
    read_workgroup_text,
    workgroup_path,
)


class Command(BaseCommand):
    help = "Inspect or initialize the local Codex workgroup coordination file."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["path", "ensure", "read"])
        parser.add_argument(
            "--codex-home", help="Override the local CODEX_HOME directory."
        )

    def handle(self, *args, **options):
        codex_home = Path(options["codex_home"]) if options.get("codex_home") else None
        action = options["action"]
        if action == "path":
            self.stdout.write(str(workgroup_path(codex_home=codex_home)))
            return
        if action == "ensure":
            self.stdout.write(str(ensure_workgroup_file(codex_home=codex_home)))
            return
        self.stdout.write(read_workgroup_text(codex_home=codex_home))
