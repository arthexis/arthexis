from __future__ import annotations

from django import template

from apps.wikis.services import fetch_wiki_summary

register = template.Library()


@register.simple_tag(name="wiki_summary")
def wiki_summary(topic: str, bridge_slug: str | None = None):
    """Return a cached wiki summary for use in templates."""

    return fetch_wiki_summary(topic, bridge_slug=bridge_slug)
