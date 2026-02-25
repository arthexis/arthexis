"""Template helpers for rendering Pyxel controls inside admin views."""

from __future__ import annotations

from django import template

from apps.pyxel.live_stats import is_local_request

register = template.Library()


@register.filter(name="pyxel_local_request")
def pyxel_local_request(request) -> bool:
    """Return whether the current request originated from a local server IP."""

    if request is None:
        return False
    return is_local_request(request)
