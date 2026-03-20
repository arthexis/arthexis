from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand

from apps.nginx.management.commands.nginx import ConfigureMixin


class Command(ConfigureMixin, BaseCommand):
    help = "Deprecated alias for `nginx --configure`."  # noqa: A003 - django requires 'help'

    def add_arguments(self, parser):
        """Accept legacy flags and forward them to ``nginx --configure``."""

        self.add_configure_arguments(parser)

    def handle(self, *args, **options):
        """Forward the legacy command to the consolidated nginx CLI."""

        self.stdout.write("`nginx_configure` is deprecated; use `nginx --configure` instead.")
        call_command(
            "nginx",
            "--configure",
            *self._forwarded_args(options),
            stdout=self.stdout,
            stderr=self.stderr,
        )

    def _forwarded_args(self, options: dict[str, object]) -> list[str]:
        """Convert parsed legacy options back into CLI flags for forwarding."""

        forwarded: list[str] = []
        if options["mode"]:
            forwarded.extend(["--mode", str(options["mode"])])
        if options["port"]:
            forwarded.extend(["--port", str(options["port"])])
        if options["role"]:
            forwarded.extend(["--role", str(options["role"])])
        if options["ip6"]:
            forwarded.append("--ip6")
        if options["remove"]:
            forwarded.append("--remove")
        if options["no_reload"]:
            forwarded.append("--no-reload")
        if options["sites_config"]:
            forwarded.extend(["--sites-config", str(options["sites_config"])])
        if options["sites_destination"]:
            forwarded.extend(["--sites-destination", str(options["sites_destination"])])
        return forwarded
