from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from apps.skills.agent_context import (
    default_agents_path,
    render_agents_context,
    write_agents_context,
)


class Command(BaseCommand):
    help = "Render or write the local dynamic AGENTS.md context for this node."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["path", "render", "write"])
        parser.add_argument(
            "--target", help="Output path for the generated AGENTS.md file."
        )

    def handle(self, *args, **options):
        action = options["action"]
        target = (
            Path(options["target"]) if options.get("target") else default_agents_path()
        )
        if action == "path":
            self.stdout.write(str(target))
            return
        if action == "render":
            self.stdout.write(render_agents_context())
            return
        result = write_agents_context(target=target)
        status = "written" if result.written else "unchanged"
        self.stdout.write(f"{result.path} ({status})")
