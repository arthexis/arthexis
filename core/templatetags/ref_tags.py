from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django import template
from django.conf import settings
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.utils.timesince import timesince

from core.models import Reference, PackageRelease
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
    return mark_safe(
        f'<img src="{ref.image.url}" width="{size}" height="{size}" alt="{ref.alt_text}" />'
    )


@register.inclusion_tag("core/footer.html", takes_context=True)
def render_footer(context):
    """Render footer links for references marked to appear there."""
    refs = Reference.objects.filter(include_in_footer=True)
    request = context.get("request")
    visible_refs = []
    for ref in refs:
        if ref.footer_visibility == Reference.FOOTER_PUBLIC:
            visible_refs.append(ref)
        elif (
            ref.footer_visibility == Reference.FOOTER_PRIVATE
            and request
            and request.user.is_authenticated
        ):
            visible_refs.append(ref)
        elif (
            ref.footer_visibility == Reference.FOOTER_STAFF
            and request
            and request.user.is_authenticated
            and request.user.is_staff
        ):
            visible_refs.append(ref)

    version = ""
    ver_path = Path(settings.BASE_DIR) / "VERSION"
    if ver_path.exists():
        version = ver_path.read_text().strip()

    revision_value = revision.get_revision()
    rev_short = revision_value[-6:] if revision_value else ""
    release_name = DEFAULT_PACKAGE.name
    release_url = None
    if version:
        release_name = f"{release_name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        release = PackageRelease.objects.filter(version=version).first()
        if release:
            release_url = reverse(
                "admin:core_packagerelease_change", args=[release.pk]
            )

    fresh_since = None
    base_dir = Path(settings.BASE_DIR)
    auto_upgrade = base_dir / "AUTO_UPGRADE"
    lock_file = base_dir / "locks" / "celery.lck"
    log_file = base_dir / "logs" / "auto-upgrade.log"
    if auto_upgrade.exists() and lock_file.exists() and log_file.exists():
        try:
            first_line = log_file.read_text().splitlines()[0]
            timestamp = first_line.split(" ", 1)[0]
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            fresh_since = timesince(dt, timezone.now())
        except Exception:
            fresh_since = None

    return {
        "footer_refs": visible_refs,
        "release_name": release_name,
        "release_url": release_url,
        "request": context.get("request"),
        "fresh_since": fresh_since,
    }

