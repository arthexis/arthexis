"""Application registry and site integration settings."""

import ast
import os
import sys
from collections.abc import Iterable

from django.contrib.sites import shortcuts as sites_shortcuts
from django.contrib.sites.requests import RequestSite

from utils.enabled_apps_lock import (
    read_enabled_apps_lock,
    read_enabled_apps_lock_direct_entries,
)
from utils.env import env_bool
from utils.role_app_profiles import (
    REQUIRED_APP_SELECTORS,
    RETIRED_RUNTIME_APP_SELECTORS,
    ROLE_DEFAULT_APP_SELECTORS,
    RoleProfile,
    close_app_dependencies,
    filter_app_selectors_with_available_dependencies,
    filter_disabled_app_selectors,
    get_feature_pack_app_selectors,
    get_role_default_app_selectors,
    normalize_feature_pack_name,
    normalize_role_profile,
    resolve_role_app_selectors,
    validate_no_deprecated_feature_packs,
    validate_required_app_selectors_available,
    validate_required_app_selectors_not_disabled,
)

from .base import BASE_DIR, HAS_DEBUG_TOOLBAR, NODE_ROLE


def _dedupe_app_entries(app_paths: Iterable[str]) -> list[str]:
    """Return app entries with exact duplicates removed while preserving order."""

    deduped: list[str] = []
    seen_entries: set[str] = set()
    for entry in app_paths:
        normalized = entry.strip()
        if normalized in seen_entries:
            continue

        seen_entries.add(normalized)
        deduped.append(normalized)

    return deduped


def _split_setting_list(value: str | None) -> tuple[str, ...]:
    """Return tokens from a comma, semicolon, or whitespace separated setting."""

    if not value:
        return ()

    normalized = value.replace(",", " ").replace(";", " ")
    return tuple(token.strip() for token in normalized.split() if token.strip())


def _split_env_lists(*names: str) -> tuple[str, ...]:
    entries: list[str] = []
    for name in names:
        entries.extend(_split_setting_list(os.environ.get(name)))
    return tuple(_dedupe_app_entries(entries))


def _validate_feature_pack_names(feature_packs: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        normalize_feature_pack_name(feature_pack) for feature_pack in feature_packs
    )


def _ordered_installed_app_entries(
    known_app_entries: Iterable[str],
    selected_app_entries: Iterable[str],
) -> list[str]:
    """Return selected app entries in the repository's stable declaration order."""

    known_entries = _dedupe_app_entries(known_app_entries)
    selected_entries = _normalize_selected_app_entries(
        selected_app_entries,
        known_entries,
    )
    selected = set(selected_entries)
    ordered = [entry for entry in known_entries if entry in selected]
    known = set(known_entries)
    ordered.extend(entry for entry in selected_entries if entry not in known)
    return ordered


def _app_entry_aliases(app_entry: str) -> tuple[str, ...]:
    aliases = [app_entry]
    if app_entry.startswith("apps."):
        aliases.append(app_entry.removeprefix("apps."))
    aliases.append(app_entry.rsplit(".", maxsplit=1)[-1])
    aliases.extend(_static_app_config_aliases(app_entry))
    return tuple(_dedupe_app_entries(aliases))


def _candidate_app_config_modules(app_entry: str) -> tuple[tuple[str, str | None], ...]:
    last_segment = app_entry.rsplit(".", maxsplit=1)[-1]
    if last_segment[:1].isupper():
        module_name, class_name = app_entry.rsplit(".", maxsplit=1)
        return ((module_name, class_name),)

    if app_entry.startswith("apps."):
        return ((f"{app_entry}.apps", None),)

    return ()


def _static_app_config_aliases(app_entry: str) -> tuple[str, ...]:
    aliases: list[str] = []
    for module_name, expected_class in _candidate_app_config_modules(app_entry):
        module_path = BASE_DIR.joinpath(*module_name.split(".")).with_suffix(".py")
        if not module_path.exists():
            continue

        try:
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if expected_class is not None and node.name != expected_class:
                continue

            literal_values: dict[str, str] = {}
            for statement in node.body:
                if not isinstance(statement, ast.Assign):
                    continue
                if not isinstance(statement.value, ast.Constant) or not isinstance(
                    statement.value.value,
                    str,
                ):
                    continue
                for target in statement.targets:
                    if isinstance(target, ast.Name) and target.id in {"label", "name"}:
                        literal_values[target.id] = statement.value.value

            aliases.extend(
                value for key in ("name", "label") if (value := literal_values.get(key))
            )

    return tuple(_dedupe_app_entries(aliases))


def _normalize_selected_app_entries(
    selected_app_entries: Iterable[str],
    known_app_entries: Iterable[str],
) -> list[str]:
    """Normalize selected app labels to the declared Django app entries."""

    alias_map = {
        alias: app_entry
        for app_entry in known_app_entries
        for alias in _app_entry_aliases(app_entry)
    }
    normalized_entries: list[str] = []
    for app_entry in selected_app_entries:
        selected_entry = app_entry.strip()
        if not selected_entry:
            continue
        if selected_entry in RETIRED_RUNTIME_APP_SELECTORS:
            continue

        normalized_entry = alias_map.get(selected_entry)
        if normalized_entry is not None:
            if normalized_entry in RETIRED_RUNTIME_APP_SELECTORS:
                continue
            normalized_entries.append(normalized_entry)
        elif "." in selected_entry:
            if selected_entry.startswith("apps.") and not _static_app_config_aliases(
                selected_entry
            ):
                continue
            normalized_entries.append(selected_entry)

    return _dedupe_app_entries(normalized_entries)


PROJECT_LOCAL_APPS = [
    "apps.actions",
    "apps.skills",
    "apps.app",
    "apps.base",
    "apps.cards",
    "apps.celery",
    "apps.certs",
    "apps.clocks",
    "apps.core",
    "apps.counters",
    "apps.credentials",
    "apps.discovery",
    "apps.emails",
    "apps.energy",
    "apps.features",
    "apps.groups",
    "apps.imager",
    "apps.locale",
    "apps.locals",
    "apps.maps",
    "apps.media",
    "apps.modules",
    "apps.nginx",
    "apps.nmcli",
    "apps.nodes",
    "apps.ocpp",
    "apps.odoo",
    "apps.ops",
    "apps.printers",
    "apps.protocols",
    "apps.release",
    "apps.reports",
    "apps.repos",
    "apps.rpiconnect",
    "apps.sensors",
    "apps.serialbridge",
    "apps.services",
    "apps.sigils",
    "apps.sites",
    "apps.terminals",
    "apps.totp",
    "apps.users",
]
ODOO_APP_SELECTOR = "apps.odoo"
SKILLS_APP_SELECTOR = "apps.skills"
OPTIONAL_PROJECT_LOCAL_APPS: list[str] = []
THIRD_PARTY_APPS = [
    "channels",
    "django_mermaid.apps.MermaidConfig",
    "django_object_actions",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "import_export",
    "parler",
]
DJANGO_CORE_APPS = [
    "django.contrib.admin",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.staticfiles",
]
PROJECT_APPS = [
    "apps.whitenoise",
    "config.auth_app.AuthConfig",
    "apps.celery.beat_app.CeleryBeatConfig",
]
ARTHEXIS_EXTERNAL_APPS: list[str] = []

_FALLBACK_APP_ENTRIES = (
    PROJECT_APPS + DJANGO_CORE_APPS + THIRD_PARTY_APPS + PROJECT_LOCAL_APPS
)


def _resolve_runtime_installed_apps() -> list[str]:
    """Resolve installed apps from environment, lock, and role inputs."""

    known_entries = _dedupe_app_entries(_FALLBACK_APP_ENTRIES)
    include_app_entries = _split_env_lists(
        "ARTHEXIS_ENABLED_APPS",
        "ARTHEXIS_INCLUDE_APPS",
    )
    disabled_app_entries = _split_env_lists("ARTHEXIS_DISABLED_APPS")
    feature_packs = _validate_feature_pack_names(
        _split_env_lists(
            "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
            "ARTHEXIS_FEATURE_PACKS",
        )
    )
    validate_no_deprecated_feature_packs(feature_packs)

    lock_entries = read_enabled_apps_lock()
    lock_direct_entries = read_enabled_apps_lock_direct_entries()
    use_lock = env_bool("ARTHEXIS_USE_ENABLED_APPS_LOCK", default=True)
    full_app_fallback = env_bool("ARTHEXIS_FULL_APP_FALLBACK", default=True)

    selected_entries: tuple[str, ...]
    if include_app_entries:
        selected_entries = include_app_entries
    elif use_lock and lock_entries:
        selected_entries = lock_entries
    elif NODE_ROLE:
        selected_entries = resolve_role_app_selectors(
            NODE_ROLE,
            feature_packs=feature_packs,
            available_selectors=known_entries,
        ).selectors
    elif full_app_fallback:
        selected_entries = tuple(known_entries)
    else:
        selected_entries = tuple(
            selector
            for selector in known_entries
            if selector in REQUIRED_APP_SELECTORS
        )

    selected_entries = tuple(
        filter_disabled_app_selectors(
            selected_entries,
            disabled_app_entries,
        )
    )
    selected_entries = tuple(
        filter_app_selectors_with_available_dependencies(
            selected_entries,
            available_selectors=known_entries,
        )
    )
    selected_entries = tuple(close_app_dependencies(selected_entries))
    selected_entries = tuple(
        entry
        for entry in selected_entries
        if entry not in RETIRED_RUNTIME_APP_SELECTORS
    )

    validate_required_app_selectors_available(known_entries)
    validate_required_app_selectors_not_disabled(
        disabled_app_entries,
        available_selectors=known_entries,
    )

    installed_apps = _ordered_installed_app_entries(known_entries, selected_entries)

    if HAS_DEBUG_TOOLBAR:
        installed_apps.append("debug_toolbar")

    if env_bool("ARTHEXIS_TESTING", default=False):
        installed_apps.append("apps.tests")

    return installed_apps


INSTALLED_APPS = _resolve_runtime_installed_apps()


def explain_enabled_apps() -> dict[str, object]:
    """Return diagnostic details for runtime app selection."""

    known_entries = _dedupe_app_entries(_FALLBACK_APP_ENTRIES)
    include_app_entries = _split_env_lists(
        "ARTHEXIS_ENABLED_APPS",
        "ARTHEXIS_INCLUDE_APPS",
    )
    disabled_app_entries = _split_env_lists("ARTHEXIS_DISABLED_APPS")
    feature_packs = _validate_feature_pack_names(
        _split_env_lists(
            "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
            "ARTHEXIS_FEATURE_PACKS",
        )
    )
    lock_entries = read_enabled_apps_lock()
    lock_direct_entries = read_enabled_apps_lock_direct_entries()

    role_resolution = None
    if NODE_ROLE:
        try:
            role_resolution = resolve_role_app_selectors(
                NODE_ROLE,
                feature_packs=feature_packs,
                available_selectors=known_entries,
            )
        except ValueError:
            role_resolution = None

    direct_entries = tuple(
        entry
        for entry in (
            *include_app_entries,
            *lock_direct_entries,
            *(
                get_role_default_app_selectors(NODE_ROLE)
                if NODE_ROLE
                else ()
            ),
            *get_feature_pack_app_selectors(feature_packs),
        )
        if entry not in RETIRED_RUNTIME_APP_SELECTORS
    )

    return {
        "known_apps": tuple(known_entries),
        "installed_apps": tuple(INSTALLED_APPS),
        "include_apps": include_app_entries,
        "disabled_apps": disabled_app_entries,
        "feature_packs": feature_packs,
        "lock_apps": lock_entries,
        "lock_direct_apps": lock_direct_entries,
        "direct_apps": _dedupe_app_entries(direct_entries),
        "role": NODE_ROLE,
        "role_profile": (
            normalize_role_profile(NODE_ROLE).value if NODE_ROLE else None
        ),
        "role_defaults": (
            get_role_default_app_selectors(NODE_ROLE) if NODE_ROLE else ()
        ),
        "role_resolution": role_resolution,
    }
