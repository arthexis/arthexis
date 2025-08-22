import base64
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import qrcode
from django import template
from django.apps import apps
from django.contrib import admin
from django.urls import reverse
from django.utils.safestring import mark_safe

from refs.models import Reference
from utils import revision

register = template.Library()


@register.filter
def is_url(value):
    """Return True if the given value looks like an HTTP/HTTPS URL."""
    try:
        result = urlparse(value)
    except Exception:
        return False
    return result.scheme in {"http", "https"} and bool(result.netloc)


@register.simple_tag
def ref_img(value, size=200, alt=None):
    """Return an <img> tag with the stored reference image for the value."""
    ref, _ = Reference.objects.get_or_create(
        value=value,
        defaults={"alt_text": alt or value, "content_type": Reference.TEXT},
    )
    alt_text = alt or ref.alt_text or "reference"
    if ref.alt_text != alt_text:
        ref.alt_text = alt_text
    ref.uses += 1
    ref.save()
    return mark_safe(
        f'<img src="{ref.image.url}" width="{size}" height="{size}" alt="{ref.alt_text}" />'
    )


@register.simple_tag(takes_context=True)
def current_page_qr(context, size=200):
    """Return an <img> tag with a QR code for the current page."""
    request = context.get("request")
    if request is None:
        return ""
    url = request.build_absolute_uri()
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    img_src = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    return mark_safe(
        f'<img src="{img_src}" alt="QR code for this page" width="{size}" height="{size}">'  # noqa: E501
    )


@register.inclusion_tag("refs/footer.html", takes_context=True)
def render_footer(context):
    """Render footer links for references marked to appear there."""
    revision_value = revision.get_revision()
    rev_short = revision_value[-6:] if revision_value else ""
    version = ""
    ver_path = Path("VERSION")
    if ver_path.exists():
        version = ver_path.read_text().strip()

    request = context.get("request")
    admin_links = []
    if request and getattr(request, "user", None) and request.user.is_staff:
        match = getattr(request, "resolver_match", None)
        if match and match.app_name:
            try:
                app_config = apps.get_app_config(match.app_name)
            except LookupError:
                app_config = None
            if app_config:
                for model in app_config.get_models():
                    if admin.site.is_registered(model):
                        name = model._meta.verbose_name_plural.title()
                        admin_links.append(
                            (
                                name,
                                reverse(
                                    f"admin:{app_config.label}_{model._meta.model_name}_changelist"
                                ),
                            )
                        )

    return {
        "footer_refs": Reference.objects.filter(include_in_footer=True),
        "revision": rev_short,
        "version": version,
        "admin_links": admin_links,
        "request": request,
    }

