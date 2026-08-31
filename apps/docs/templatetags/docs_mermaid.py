"""Template tags for Mermaid assets in docs views."""

from __future__ import annotations

import json

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.safestring import mark_safe

from django_mermaid.templatetags.mermaid import (
    DEFAULT_THEME,
    DEFAULT_USE_CDN,
    DEFAULT_VERSION,
    MERMAID_CDN,
)

register = template.Library()


@register.simple_tag
@mark_safe
def mermaid_assets() -> str:
    """Return Mermaid script tags and initialization without diagram markup."""

    version = getattr(settings, "MERMAID_VERSION", DEFAULT_VERSION)
    use_cdn = getattr(settings, "MERMAID_USE_CDN", DEFAULT_USE_CDN)
    theme = getattr(settings, "MERMAID_THEME", DEFAULT_THEME)
    theme_variables = (
        getattr(settings, "MERMAID_THEME_VARIABLES", {}) if theme == "base" else {}
    )
    mermaid_uri = MERMAID_CDN % version if use_cdn else static(
        "mermaid/%s/mermaid.js" % version
    )
    init_properties = {
        "startOnLoad": True,
        "theme": theme,
        "themeVariables": theme_variables,
    }
    return (
        f'<script src="{mermaid_uri}"></script>'
        f"<script>mermaid.initialize({json.dumps(init_properties)});</script>"
    )
