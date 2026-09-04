"""Django settings integration for optional extension repository checkouts."""

from __future__ import annotations

import os
import re

from django.core.exceptions import ImproperlyConfigured

from utils.extensions import ExtensionError, activate_extension_paths

from .apps import INSTALLED_APPS as CORE_INSTALLED_APPS
from .base import BASE_DIR


def _split_disabled_apps() -> set[str]:
    values: list[str] = []
    for name in ("ARTHEXIS_ROLE_APP_DISABLED_APPS", "ARTHEXIS_DISABLED_APPS"):
        values.extend(
            part
            for part in re.split(r"[,;\s]+", os.environ.get(name, ""))
            if part
        )
    aliases: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned:
            continue
        aliases.add(cleaned)
        aliases.add(cleaned.rsplit(".", 1)[-1])
        if cleaned.startswith("apps."):
            aliases.add(cleaned.removeprefix("apps."))
    return aliases


def _app_aliases(app_entry: str) -> set[str]:
    aliases = {app_entry, app_entry.rsplit(".", 1)[-1]}
    if app_entry.startswith("apps."):
        aliases.add(app_entry.removeprefix("apps."))
    return aliases


try:
    _EXTENSION_MANIFESTS = activate_extension_paths(BASE_DIR)
except ExtensionError as exc:
    raise ImproperlyConfigured(str(exc)) from exc

_DISABLED_EXTENSION_APPS = _split_disabled_apps()
ARTHEXIS_EXTERNAL_APPS = [
    app_entry
    for manifest in _EXTENSION_MANIFESTS
    for app_entry in manifest.django_apps
    if not (_app_aliases(app_entry) & _DISABLED_EXTENSION_APPS)
]

_combined_apps = list(dict.fromkeys([*CORE_INSTALLED_APPS, *ARTHEXIS_EXTERNAL_APPS]))
_combined_aliases = {
    alias for app_entry in _combined_apps for alias in _app_aliases(app_entry)
}
for manifest in _EXTENSION_MANIFESTS:
    missing = [
        requirement
        for requirement in manifest.requires_apps
        if not (_app_aliases(requirement) & _combined_aliases)
    ]
    if missing:
        requirements = ", ".join(missing)
        raise ImproperlyConfigured(
            f"Extension {manifest.name!r} requires unavailable Django apps: "
            f"{requirements}."
        )

INSTALLED_APPS = _combined_apps
