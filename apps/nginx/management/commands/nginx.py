from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


class ConfigureMixin:
    """Shared implementation for nginx configuration commands."""

    def add_configure_arguments(self, parser) -> None:
        """Register arguments used to apply or remove managed nginx configuration."""

        parser.add_argument("--mode", default=None, help="nginx mode (internal or public)")
        parser.add_argument("--port", type=int, default=None, help="Application port proxied by nginx")
        parser.add_argument("--role", default=None, help="Role label to persist alongside the configuration")
        parser.add_argument("--ip6", action="store_true", help="Include IPv6 listeners in the rendered configuration")
        parser.add_argument("--remove", action="store_true", help="Remove nginx configuration instead of applying it")
        parser.add_argument("--no-reload", action="store_true", help="Skip nginx reload/restart after applying changes")
        parser.add_argument(
            "--sites-config",
            default=None,
            help="Optional override for the staged site configuration JSON.",
        )
        parser.add_argument(
            "--sites-destination",
            default=None,
            help="Optional override for the managed site destination path.",
        )

    def run_configure(self, options: dict[str, object]) -> None:
        """Apply or remove the managed nginx configuration using the provided options."""

        config = SiteConfiguration.get_default()

        if options["mode"]:
            config.mode = str(options["mode"]).lower()
        if options["port"]:
            config.port = options["port"]
        if options["role"]:
            config.role = options["role"]
        if options["ip6"]:
            config.include_ipv6 = True
        if options["sites_config"]:
            config.site_entries_path = options["sites_config"]
        if options["sites_destination"]:
            config.site_destination = options["sites_destination"]

        config.enabled = not options["remove"]
        config.save()

        reload = not options["no_reload"]

        try:
            result = config.apply(reload=reload, remove=options["remove"])
        except NginxUnavailableError as exc:  # pragma: no cover - requires system nginx
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx applied the configuration, but validation was skipped or failed.")
        if not result.reloaded:
            self.stdout.write("nginx was not reloaded automatically; check the service status.")

        if options["sites_config"]:
            self.stdout.write(f"Managed site definitions read from {Path(config.site_entries_path).resolve()}")
        if options["sites_destination"]:
            self.stdout.write(f"Managed sites written to {config.site_destination}")


class Command(ConfigureMixin, BaseCommand):
    help = "Manage nginx operations for this node."  # noqa: A003 - django requires 'help'

    def add_arguments(self, parser):
        """Register top-level nginx actions and configuration flags."""

        parser.add_argument(
            "--configure",
            action="store_true",
            help="Apply or remove the managed nginx configuration.",
        )
        self.add_configure_arguments(parser)

    def handle(self, *args, **options):
        """Dispatch nginx actions from the consolidated command surface."""

        if options["configure"]:
            self.run_configure(options)
            return

        raise CommandError("No nginx action was selected. Use --configure.")
