from __future__ import annotations

from pathlib import Path
import re

from django.conf import settings

from apps.release.models import PackageRelease


class NameRepresentationMixin:
    """Provide a name-based ``__str__`` for models with a ``name`` field."""

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return the model's name."""
        return self.name


ROLE_RENAMES: dict[str, str] = {"Constellation": "Watchtower"}
ROLE_ACRONYMS: dict[str, str] = {
    "Terminal": "TERM",
    "Control": "CTRL",
    "Satellite": "SATL",
    "Watchtower": "WTTW",
    "Constellation": "CONS",
}


def _upgrade_in_progress() -> bool:
    """Return True when a local upgrade lock is present."""
    lock_file = Path(settings.BASE_DIR) / ".locks" / "upgrade_in_progress.lck"
    return lock_file.exists()


def _format_upgrade_body(version: str, revision: str) -> str:
    """Return a display string summarizing version and revision."""
    version = (version or "").strip()
    revision = (revision or "").strip()
    parts: list[str] = []
    if version:
        normalized = version.lstrip("vV") or version
        base_version = normalized.rstrip("+")
        display_version = normalized
        if (
            base_version
            and revision
            and not PackageRelease.matches_revision(base_version, revision)
            and not normalized.endswith("+")
        ):
            display_version = f"{display_version}+"
        parts.append(f"v{display_version}")
    if revision:
        rev_clean = re.sub(r"[^0-9A-Za-z]", "", revision)
        rev_short = (rev_clean[-6:] if rev_clean else revision[-6:])
        parts.append(f"r{rev_short}")
    return " ".join(parts).strip()
