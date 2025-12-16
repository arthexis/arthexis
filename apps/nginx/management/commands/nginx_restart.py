from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.nginx.models import SiteConfiguration
from apps.nginx.services import NginxUnavailableError


class Command(BaseCommand):
    help = "Validate and restart nginx using managed settings."  # noqa: A003 - django requires 'help'

    def handle(self, *args, **options):
        config = SiteConfiguration.get_default()
        try:
            result = config.validate_only()
        except NginxUnavailableError as exc:  # pragma: no cover - requires system nginx
            raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(result.message))
        if not result.validated:
            self.stdout.write("nginx configuration test failed; review the system logs for details.")
        if not result.reloaded:
            self.stdout.write("nginx could not be reloaded automatically; check the service status.")
