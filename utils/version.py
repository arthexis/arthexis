from __future__ import annotations

from pathlib import Path

from django.conf import settings


def get_version() -> str:
    version_path = Path(settings.BASE_DIR) / "VERSION"
    if version_path.exists():
        return version_path.read_text(encoding="utf-8").strip()
    return ""
