from __future__ import annotations

from pathlib import Path
import importlib
import re

from django.conf import settings

class NameRepresentationMixin:
    """Provide a name-based ``__str__`` for models with a ``name`` field."""

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return the model's name."""
        return self.name


ROLE_RENAMES: dict[str, str] = {"Constellation": "Watchtower"}
ROLE_ACRONYMS: dict[str, str] = {
    "Terminal": "TRMN",
    "Control": "CTRL",
    "Satellite": "STLT",
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
            and not _matches_release_revision(base_version, revision)
            and not normalized.endswith("+")
        ):
            display_version = f"{display_version}+"
        parts.append(f"v{display_version}")
    if revision:
        rev_clean = re.sub(r"[^0-9A-Za-z]", "", revision)
        rev_short = (rev_clean[-6:] if rev_clean else revision[-6:])
        parts.append(f"r{rev_short}")
    return " ".join(parts).strip()


def _matches_release_revision(version: str, revision: str) -> bool:
    """Return whether revision matches the given release version when available.

    The ``apps.release`` application can be excluded by enabled-app locks. In
    that configuration, importing concrete release models at module import time
    triggers ``RuntimeError`` before Django startup. This helper performs a
    guarded runtime import so callers can still format upgrade text.
    """

    try:
        package_release_module = importlib.import_module("apps.release.models")
        package_release = getattr(package_release_module, "PackageRelease", None)
    except (ImportError, RuntimeError) as exc:
        if isinstance(exc, RuntimeError) and "isn't in an application in INSTALLED_APPS" not in str(exc):
            raise
        return False

    if package_release is None:
        return False

    return bool(package_release.matches_revision(version, revision))
