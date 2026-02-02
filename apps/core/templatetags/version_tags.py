from __future__ import annotations

from pathlib import Path

from django import template
from django.conf import settings

from utils import revision

register = template.Library()


def _read_version() -> str:
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return ""


@register.simple_tag
def version_check_info() -> dict[str, str]:
    """Return the local version metadata for the version check banner."""

    return {
        "version": _read_version(),
        "revision": (revision.get_revision() or "").strip(),
    }
