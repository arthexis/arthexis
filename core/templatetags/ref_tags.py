from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

from core.models import Reference, PackageRelease
from core.reference_utils import filter_visible_references
from core.release import DEFAULT_PACKAGE
from utils import revision

register = template.Library()


INSTANCE_START = timezone.now()


@register.simple_tag
def ref_img(value, size=200, alt=None):
    """Return an <img> tag with the stored reference image for the value."""
    ref, created = Reference.objects.get_or_create(
        value=value, defaults={"alt_text": alt or value}
    )
    alt_text = alt or ref.alt_text or "reference"
    if ref.alt_text != alt_text:
        ref.alt_text = alt_text
    ref.uses += 1
    ref.save()
    return format_html(
        '<img src="{}" width="{}" height="{}" alt="{}" />',
        ref.image.url,
        size,
        size,
        ref.alt_text,
    )


@register.inclusion_tag("core/footer.html", takes_context=True)
def render_footer(context):
    """Render footer links for references marked to appear there."""
    refs = Reference.objects.filter(include_in_footer=True).prefetch_related(
        "roles", "features", "sites"
    )
    request = context.get("request")
    visible_refs = filter_visible_references(
        refs,
        request=request,
        site=context.get("badge_site"),
        node=context.get("badge_node"),
    )

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

    if version:
        release_name = f"{release_name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        if release:
            release_url = reverse("admin:core_packagerelease_change", args=[release.pk])

    base_dir = Path(settings.BASE_DIR)
    log_file = base_dir / "logs" / "auto-upgrade.log"

    latest = INSTANCE_START
    if log_file.exists():
        try:
            last_line = log_file.read_text().splitlines()[-1]
            timestamp = last_line.split(" ", 1)[0]
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            if dt > latest:
                latest = dt
        except Exception:
            pass

    fresh_since = timezone.localtime(latest).strftime("%Y-%m-%d %H:%M")

    return {
        "footer_refs": visible_refs,
        "release_name": release_name,
        "release_url": release_url,
        "request": context.get("request"),
        "fresh_since": fresh_since,
    }
