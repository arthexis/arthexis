from __future__ import annotations

import os
import shutil
import subprocess
import sys
import webbrowser

from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand, CommandError


def _ui_available() -> bool:
    if sys.platform.startswith(("win", "darwin")):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _build_site_url(site: Site) -> str:
    domain = (site.domain or "").strip()
    if not domain:
        raise CommandError("The current site is missing a domain.")
    if "://" in domain:
        return domain

    scheme = "https" if getattr(site, "require_https", False) else "http"
    return f"{scheme}://{domain}"


class Command(BaseCommand):
    help = "Open the public site in a browser."

    def handle(self, *args, **options):  # type: ignore[override]
        site = Site.objects.get_current()
        url = _build_site_url(site)

        if _ui_available():
            self.stdout.write(f"Opening {url} in the default browser...")
            if not webbrowser.open(url, new=2):
                raise CommandError("Unable to open the default browser.")
            return

        if not shutil.which("lynx"):
            raise CommandError("lynx is required when no UI is available.")

        self.stdout.write(f"Opening {url} in lynx...")
        result = subprocess.run(["lynx", url], check=False)
        if result.returncode != 0:
            raise CommandError("lynx exited with a non-zero status.")
