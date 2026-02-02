from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django import template
from django.conf import settings
from django.urls import reverse
from django.db.models.functions import Length
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.utils.html import format_html

from apps.release.models import PackageRelease
from apps.links.models import Reference
from apps.links.reference_utils import filter_visible_references
from apps.modules.models import Module
from apps.release.release import DEFAULT_PACKAGE
from utils import revision

register = template.Library()


INSTANCE_START = timezone.now()


def _get_current_module(request):
    if not request:
        return None
    current = getattr(request, "current_module", None)
    if current is not None:
        return current
    path = getattr(request, "path", "")
    if not path:
        return None
    try:
        modules = (
            Module.objects.filter(is_deleted=False)
            .only("id", "path")
            .annotate(path_length=Length("path"))
            .order_by("-path_length")
        )
    except (OperationalError, ProgrammingError):
        return None
    for module in modules:
        if path.startswith(module.path):
            return module
    return None


def build_footer_context(
    *, request=None, badge_site=None, badge_node=None, force_footer=False, module=None
):
    """Return footer rendering context shared across templates and API views."""

    refs = Reference.objects.filter(include_in_footer=True).prefetch_related(
        "roles", "features", "sites", "footer_modules"
    )
    current_module = module or _get_current_module(request)
    if current_module is not None:
        module_refs = refs.filter(footer_modules=current_module).distinct()
        if module_refs.exists():
            refs = module_refs
        else:
            refs = refs.filter(footer_modules__isnull=True)
    else:
        refs = refs.filter(footer_modules__isnull=True)

    visible_refs = filter_visible_references(
        refs,
        request=request,
        site=badge_site,
        node=badge_node,
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

    base_dir = Path(settings.BASE_DIR)
    log_file = base_dir / "logs" / "auto-upgrade.log"

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
                dt = datetime.fromisoformat(timestamp)
            except ValueError:
                continue

            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=dt_timezone.utc)
            latest = dt
            break

        if latest is None:
            for line in reversed(lines):
                try:
                    timestamp, _ = line.split(" ", 1)
                    dt = datetime.fromisoformat(timestamp)
                except ValueError:
                    continue

                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=dt_timezone.utc)

                if latest is None or dt > latest:
                    latest = dt

    fresh_since = None
    if latest is not None:
        fresh_since = timezone.localtime(latest).strftime("%Y-%m-%d %H:%M")

    has_release_info = bool(version or revision_value or fresh_since)

    if version:
        release_name = f"{release_name}-{version}"
        if rev_short:
            release_name = f"{release_name}-{rev_short}"
        if release:
            release_url = reverse("admin:release_packagerelease_change", args=[release.pk])

    show_footer = force_footer or bool(visible_refs) or has_release_info
    if not show_footer:
        return {
            "footer_refs": [],
            "request": request,
            "show_footer": False,
            "show_release": False,
        }

    return {
        "footer_refs": visible_refs,
        "show_footer": True,
        "current_module": current_module,
        "release_name": release_name,
        "release_url": release_url,
        "request": request,
        "fresh_since": fresh_since,
        "show_release": has_release_info,
    }


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
        ref.image_url,
        size,
        size,
        ref.alt_text,
    )


@register.inclusion_tag("core/footer.html", takes_context=True)
def render_footer(context):
    """Render footer links for references marked to appear there."""

    return build_footer_context(
        request=context.get("request"),
        badge_site=context.get("badge_site"),
        badge_node=context.get("badge_node"),
        force_footer=bool(context.get("force_footer")),
        module=context.get("current_module"),
    )
