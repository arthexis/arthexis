from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django import template
from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse
from django.utils.html import format_html
from django.utils import timezone

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
    refs = (
        Reference.objects.filter(include_in_footer=True)
        .prefetch_related("roles", "features", "sites")
    )
    request = context.get("request")
    site = context.get("badge_site")
    if not site and request:
        try:
            host = request.get_host().split(":")[0]
        except Exception:
            host = ""
        if host:
            site = Site.objects.filter(domain__iexact=host).first()
    site_id = site.pk if site else None

    node = context.get("badge_node")
    if node is None:
        try:
            from nodes.models import Node

            node = Node.get_local()
        except Exception:
            node = None
    node_role_id = getattr(node, "role_id", None)
    node_feature_ids: set[int] = set()
    if node is not None:
        features_manager = getattr(node, "features", None)
        if features_manager is not None:
            try:
                node_feature_ids = set(
                    features_manager.values_list("pk", flat=True)
                )
            except Exception:
                node_feature_ids = set()

    visible_refs = []
    for ref in refs:
        required_roles = {role.pk for role in ref.roles.all()}
        required_features = {feature.pk for feature in ref.features.all()}
        required_sites = {current_site.pk for current_site in ref.sites.all()}

        if required_roles or required_features or required_sites:
            allowed = False
            if (
                required_roles
                and node_role_id
                and node_role_id in required_roles
            ):
                allowed = True
            elif (
                required_features
                and node_feature_ids
                and node_feature_ids.intersection(required_features)
            ):
                allowed = True
            elif required_sites and site_id and site_id in required_sites:
                allowed = True

            if not allowed:
                continue

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
