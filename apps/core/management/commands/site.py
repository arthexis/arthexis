import shutil
import subprocess
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

WEB_SITE_NAME = "arthexis"
WEB_HOST = "127.0.0.1"
WEB_PORT = 8888
WEB_HEALTH_PATH = "/health/"


def _normalize_domain(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise CommandError("site domain cannot be empty")
    parsed = urlsplit(candidate if "://" in candidate else f"//{candidate}")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise CommandError("site must be a hostname or origin without a path")
    if parsed.username or parsed.password:
        raise CommandError("site must not contain credentials")
    host = parsed.hostname
    if not host:
        raise CommandError(f"invalid site domain: {value!r}")
    return host.rstrip(".").lower()


def _gway_site_exists(gway: str) -> bool:
    result = subprocess.run(
        [gway, "web", "site", WEB_SITE_NAME],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.returncode == 0


def _forward_to_gway(site: Site, *site_arguments: str) -> bool:
    """Publish the Django Site as portable web intent when GWAY is available."""
    gway = shutil.which("gway")
    if gway is None:
        return False

    action = "--update" if _gway_site_exists(gway) else "--create"
    subprocess.run(
        [
            gway,
            "web",
            "site",
            WEB_SITE_NAME,
            action,
            "--domain",
            site.domain,
            "--host",
            WEB_HOST,
            "--port",
            str(WEB_PORT),
            "--health-path",
            WEB_HEALTH_PATH,
            *site_arguments,
        ],
        check=True,
        text=True,
    )
    return True


class Command(BaseCommand):
    help = "Show or configure the canonical Arthexis site"

    def add_arguments(self, parser):
        parser.add_argument(
            "domain",
            nargs="?",
            help="Canonical site hostname (for example example.com)",
        )
        parser.add_argument(
            "--name",
            help="Human-readable Django Site name; defaults to the domain when first configured",
        )
        parser.add_argument(
            "--no-refresh-node",
            action="store_true",
            help="Do not refresh local node registration after changing the site",
        )
        parser.add_argument(
            "site_arguments",
            nargs="*",
            help="Additional arguments after '--' are forwarded unchanged to 'gway web site'",
        )

    def handle(self, *args, **options):
        site_id = getattr(settings, "SITE_ID", 1)
        site, created = Site.objects.get_or_create(
            pk=site_id,
            defaults={"domain": "example.com", "name": "example.com"},
        )

        changed_fields: list[str] = []
        domain = options["domain"]
        if domain is not None:
            normalized = _normalize_domain(domain)
            if site.domain != normalized:
                site.domain = normalized
                changed_fields.append("domain")
            if created and not options["name"]:
                site.name = normalized
                changed_fields.append("name")

        name = options["name"]
        if name is not None:
            normalized_name = name.strip()
            if not normalized_name:
                raise CommandError("site name cannot be empty")
            if site.name != normalized_name:
                site.name = normalized_name
                changed_fields.append("name")

        if changed_fields:
            site.save(update_fields=sorted(set(changed_fields)))
            Site.objects.clear_cache()
            if not options["no_refresh_node"]:
                call_command(
                    "ensure_local_node", stdout=self.stdout, stderr=self.stderr
                )

        _forward_to_gway(site, *options["site_arguments"])

        self.stdout.write(f"id: {site.pk}")
        self.stdout.write(f"domain: {site.domain}")
        self.stdout.write(f"name: {site.name}")
        self.stdout.write("web:")
        self.stdout.write(f"  name: {WEB_SITE_NAME}")
        self.stdout.write(f"  domain: {site.domain}")
        self.stdout.write(f"  host: {WEB_HOST}")
        self.stdout.write(f"  port: {WEB_PORT}")
        self.stdout.write(f"  health: {WEB_HEALTH_PATH}")
