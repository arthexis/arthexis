from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.management.commands._config_selection import get_configurations
from apps.nginx.services import NginxUnavailableError, ValidationError


class Command(BaseCommand):
    help = "Apply nginx configuration for selected site configurations."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            default="",
            help="Comma-separated SiteConfiguration ids to apply.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Apply all site configurations.",
        )
        parser.add_argument(
            "--no-reload",
            action="store_true",
            help="Skip nginx reload/restart after applying changes.",
        )

    def handle(self, *args, **options):
        queryset = get_configurations(options["ids"], select_all=options["all"])
        configs = list(queryset)
        if not configs:
            raise CommandError("No site configurations selected. Use --ids or --all.")

        errors: list[str] = []
        reload = not options["no_reload"]

        for config in configs:
            if config.protocol == "https" and config.certificate is None:
                message = (
                    f"{config}: HTTPS requires a linked certificate. "
                    "Run generate_certs or assign one before applying."
                )
                self.stderr.write(self.style.ERROR(message))
                errors.append(message)
                continue

            try:
                result = config.apply(reload=reload)
            except (NginxUnavailableError, ValidationError) as exc:
                message = f"{config}: {exc}"
                self.stderr.write(self.style.ERROR(message))
                errors.append(message)
                continue

            self.stdout.write(self.style.SUCCESS(f"{config}: {result.message}"))
            if not result.validated:
                self.stdout.write(
                    f"{config}: nginx configuration applied but validation was skipped or failed."
                )
            if not result.reloaded:
                self.stdout.write(
                    f"{config}: nginx reload/start did not complete automatically; check the service status."
                )

        if errors:
            raise CommandError("One or more configurations failed. Review the output above.")
