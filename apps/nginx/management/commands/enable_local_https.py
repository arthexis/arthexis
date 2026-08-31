from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Provision a self-signed certificate and nginx config for https://localhost."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Skip nginx reload/restart after applying changes.",
        )
        parser.add_argument(
            "--no-sudo",
            action="store_true",
            help="Generate the local certificate without sudo.",
        )

    def handle(self, *args, **options):
        cmd_args = ["--enable", "--local"]
        if options["no_reload"]:
            cmd_args.append("--no-reload")
        if options["no_sudo"]:
            cmd_args.append("--no-sudo")
        call_command("https", *cmd_args)
