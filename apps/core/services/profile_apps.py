"""Helpers for app-profile aware operational checks."""

from __future__ import annotations

from collections.abc import Iterable

from django.conf import settings

from config.settings.apps import _dedupe_app_entries, _static_app_config_aliases


def _operational_app_aliases(app_entry: str) -> tuple[str, ...]:
    """Return full app identifiers that are safe for runtime profile gates."""

    normalized = app_entry.strip()
    aliases = [normalized]
    aliases.extend(
        alias
        for alias in _static_app_config_aliases(normalized)
        if "." in alias or alias.startswith("apps.")
    )
    return tuple(_dedupe_app_entries(aliases))


def installed_app_aliases(
    installed_apps: Iterable[str] | None = None,
) -> frozenset[str]:
    """Return installed app selectors plus their known aliases."""

    entries = settings.INSTALLED_APPS if installed_apps is None else installed_apps
    aliases: set[str] = set()
    for entry in entries:
        normalized = str(entry).strip()
        if not normalized:
            continue
        aliases.update(_operational_app_aliases(normalized))
    return frozenset(aliases)


def app_selector_installed(
    app_selector: str,
    *,
    installed_apps: Iterable[str] | None = None,
) -> bool:
    """Return whether an app selector is installed in the current profile."""

    selector_aliases = set(_operational_app_aliases(app_selector))
    return bool(selector_aliases & installed_app_aliases(installed_apps))


def profile_skip_reason(
    *,
    app_selector: str | None = None,
    node_roles: Iterable[str] = (),
    installed_apps: Iterable[str] | None = None,
    node_role: str | None = None,
) -> str | None:
    """Return a human-readable skip reason for profile-gated checks."""

    if app_selector and not app_selector_installed(
        app_selector,
        installed_apps=installed_apps,
    ):
        return f"{app_selector} is not installed for this node profile"

    expected_roles = tuple(role.strip() for role in node_roles if str(role).strip())
    if not expected_roles:
        return None

    configured_role = (
        str(settings.NODE_ROLE if node_role is None else node_role).strip() or "unknown"
    )
    expected_lookup = {role.casefold() for role in expected_roles}
    if configured_role.casefold() in expected_lookup:
        return None

    expected_display = ", ".join(expected_roles)
    return f"node role {configured_role} is not eligible; expected: {expected_display}"
