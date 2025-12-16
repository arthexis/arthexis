from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError, ValidationError


class Command(BaseCommand):
    help = "Apply or remove the managed nginx configuration."  # noqa: A003 - django requires 'help'

    def add_arguments(self, parser):
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

    def handle(self, *args, **options):
        config = SiteConfiguration.get_default()

        if options["mode"]:
            config.mode = options["mode"].lower()
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
            raise CommandError(str(exc))
        except ValidationError as exc:
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx configuration applied but validation was skipped or failed.")
        if not result.reloaded:
            self.stdout.write("nginx reload/start did not complete automatically; check the service status.")

        if options["sites_config"]:
            self.stdout.write(f"Managed site definitions read from {Path(config.site_entries_path).resolve()}")
        if options["sites_destination"]:
            self.stdout.write(f"Managed sites written to {config.site_destination}")
