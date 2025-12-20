from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.management.commands._config_selection import get_configurations
from apps.nginx.models import SiteConfiguration


class Command(BaseCommand):
    help = "Configure selected nginx site configurations."  # noqa: A003

    def add_arguments(self, parser):
        parser.add_argument(
            "--ids",
            default="",
            help="Comma-separated SiteConfiguration ids to update.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Update all site configurations.",
        )

        protocol_group = parser.add_mutually_exclusive_group(required=True)
        protocol_group.add_argument(
            "--http",
            action="store_const",
            const="http",
            dest="protocol",
            help="Set the site configuration protocol to HTTP.",
        )
        protocol_group.add_argument(
            "--https",
            action="store_const",
            const="https",
            dest="protocol",
            help="Set the site configuration protocol to HTTPS.",
        )

    def handle(self, *args, **options):
        queryset = get_configurations(options["ids"], select_all=options["all"])
        configs = list(queryset)
        if not configs:
            self._render_available_sites()
            raise CommandError("No site configurations selected. Use --ids or --all.")

        protocol = options["protocol"]
        updated = 0
        for config in configs:
            if config.protocol == protocol:
                self.stdout.write(f"{config}: Protocol already set to {protocol}.")
                continue

            previous = config.protocol
            config.protocol = protocol
            config.save(update_fields=["protocol"])
            updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"{config}: Protocol updated from {previous} to {protocol}."
                )
            )

        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"Updated {updated} of {len(configs)} site configuration(s)."
            )
        )

    def _render_available_sites(self) -> None:
        available = list(SiteConfiguration.objects.all().order_by("pk"))
        if not available:
            self.stdout.write("No site configurations are available.")
            return
        self.stdout.write("Available site configurations:")
        for config in available:
            name = config.name or "unnamed"
            self.stdout.write(f"  [{config.pk}] {name}")
