from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from pathlib import Path

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.release import DEFAULT_PACKAGE
from apps.release.models import PackageRelease
from utils import revision

register = template.Library()


def build_footer_context(*, request=None, force_footer=False, **_kwargs):
    """Return footer rendering context without generic reference links."""

    version = ""
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    if ver_path.exists():
        version = ver_path.read_text().strip()

    revision_value = (revision.get_revision() or "").strip()
    release_name = DEFAULT_PACKAGE.name
    release_url = None
    release = None
    release_revision = ""
    if version:
        release = PackageRelease.objects.filter(version=version).first()
        if release and release.revision:
            release_revision = release.revision.strip()

    rev_short = ""
    if revision_value and revision_value != release_revision:
        rev_short = revision_value[-6:]

    log_file = Path(settings.BASE_DIR) / "logs" / "auto-upgrade.log"
    latest = None
    if log_file.exists():
        try:
            lines = log_file.read_text().splitlines()
        except Exception:
            lines = []

        for line in reversed(lines):
            try:
                timestamp, message = line.split(" ", 1)
            except ValueError:
                continue
            if "running: ./upgrade.sh" not in message:
                continue
            try:
                latest = datetime.fromisoformat(timestamp)
            except ValueError:
                continue
            break

        if latest is None:
            for line in reversed(lines):
                try:
                    timestamp, _message = line.split(" ", 1)
                    candidate = datetime.fromisoformat(timestamp)
                except ValueError:
                    continue
                if latest is None or candidate > latest:
                    latest = candidate

    fresh_since = None
    if latest is not None:
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=dt_timezone.utc)
        fresh_since = timezone.localtime(latest).strftime("%Y-%m-%d %H:%M")

    has_release_info = bool(version or revision_value or fresh_since)
    if version:
        release_name = f"{release_name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        if release:
            release_url = reverse("admin:release_packagerelease_change", args=[release.pk])

    show_footer = force_footer or has_release_info
    return {
        "footer_refs": [],
        "show_footer": show_footer,
        "release_name": release_name,
        "release_url": release_url,
        "request": request,
        "fresh_since": fresh_since,
        "show_release": has_release_info,
    }


@register.inclusion_tag("core/footer.html", takes_context=True)
def render_footer(context):
    """Render the footer without generic link references."""

    return build_footer_context(
        request=context.get("request"),
        force_footer=bool(context.get("force_footer")),
    )
