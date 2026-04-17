from __future__ import annotations

from django import template

from apps.sigils.sigil_resolver import resolve_user_safe_sigils

register = template.Library()


@register.simple_tag(takes_context=True)
def sigil_expr(context, expression: str, current=None):
    """Resolve a single sigil expression using user-safe policy."""

    request = context.get("request")
    active_object = current or context.get("object")
    source = expression or ""
    if "[" not in source:
        source = f"[{source}]"
    return resolve_user_safe_sigils(source, current=active_object, request=request)


@register.filter(name="sigils")
def sigils(value, current=None):
    """Resolve placeholder-rich text using user-safe policy."""

    source = "" if value is None else str(value)
    return resolve_user_safe_sigils(source, current=current)
