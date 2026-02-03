from __future__ import annotations

from django import template

from utils import revision
from utils.version import get_version

register = template.Library()


@register.simple_tag
def version_check_info() -> dict[str, str]:
    """Return the local version metadata for the version check banner."""

    return {
        "version": get_version(),
        "revision": revision.get_revision(),
    }
