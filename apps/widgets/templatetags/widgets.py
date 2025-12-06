from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from apps.widgets.services import render_zone_html

register = template.Library()


@register.simple_tag(takes_context=True)
def render_widgets(context, zone_slug: str, **kwargs):
    request = context.get("request")
    if request is None:
        return ""

    html = render_zone_html(request=request, zone_slug=zone_slug, extra_context=kwargs)
    return mark_safe(html)
