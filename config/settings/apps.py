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
    "apps.apis",
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
    "apps.dns",
    "apps.docs",
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
    "apps.summary",
    "apps.terminals",
    "apps.totp",
    "apps.users",
]
ODOO_APP_SELECTOR = "apps.odoo"
SCREENS_APP_SELECTOR = "apps.screens"
SKILLS_APP_SELECTOR = "apps.skills"
OPTIONAL_PROJECT_LOCAL_APPS = [
    SCREENS_APP_SELECTOR,
]
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
_BUILT_IN_APP_ENTRIES = (
    PROJECT_APPS
    + DJANGO_CORE_APPS
    + THIRD_PARTY_APPS
    + PROJECT_LOCAL_APPS
    + OPTIONAL_PROJECT_LOCAL_APPS
)
_BASELINE_APP_ENTRIES = PROJECT_APPS + DJANGO_CORE_APPS + THIRD_PARTY_APPS


def _resolve_installed_app_entries(
    *,
    node_role: str,
    profile_enabled: bool,
    enabled_app_lock_entries: Iterable[str] | None,
    feature_packs: Iterable[str] = (),
    disabled_apps: Iterable[str] = (),
    known_app_entries: Iterable[str] = _BUILT_IN_APP_ENTRIES,
) -> list[str]:
    """Resolve built-in installed app entries from role/profile inputs."""

    known_entries = _dedupe_app_entries(known_app_entries)
    feature_pack_values = tuple(feature_packs)
    if known_entries == _dedupe_app_entries(_BUILT_IN_APP_ENTRIES):
        fallback_entries = _dedupe_app_entries(_FALLBACK_APP_ENTRIES)
    else:
        fallback_entries = known_entries
    validate_required_app_selectors_not_disabled(disabled_apps)
    if enabled_app_lock_entries is not None:
        validate_no_deprecated_feature_packs(feature_pack_values)
        normalized_lock_entries = _normalize_selected_app_entries(
            sorted(enabled_app_lock_entries),
            known_entries,
        )
        selected_entries = close_app_dependencies(
            filter_disabled_app_selectors(
                (
                    *_BASELINE_APP_ENTRIES,
                    *REQUIRED_APP_SELECTORS.keys(),
                    *normalized_lock_entries,
                ),
                disabled_apps,
            ),
        )
        selected_entries = filter_disabled_app_selectors(
            selected_entries,
            disabled_apps,
        )
        selected_entries = filter_app_selectors_with_available_dependencies(
            selected_entries,
        )
        validate_required_app_selectors_available(selected_entries)
    elif profile_enabled:
        validate_no_deprecated_feature_packs(feature_pack_values)
        try:
            normalized_role = normalize_role_profile(node_role)
        except ValueError:
            selected_entries = fallback_entries
        else:
            selected_entries = resolve_role_app_selectors(
                normalized_role,
                feature_packs=feature_pack_values,
                disabled_apps=disabled_apps,
            )
    else:
        selected_entries = fallback_entries

    return _ordered_installed_app_entries(known_entries, selected_entries)


_ENABLED_APP_LOCK_ENTRIES = read_enabled_apps_lock(BASE_DIR)
_ENABLED_APP_LOCK_DIRECT_ENTRIES = read_enabled_apps_lock_direct_entries(BASE_DIR)
_ROLE_APP_FEATURE_PACKS = _split_env_lists(
    "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
    "ARTHEXIS_FEATURE_PACKS",
)
_ROLE_APP_DISABLED_APPS = _split_env_lists(
    "ARTHEXIS_ROLE_APP_DISABLED_APPS",
    "ARTHEXIS_DISABLED_APPS",
)
ROLE_APP_PROFILES_ENABLED = env_bool("ARTHEXIS_ROLE_APP_PROFILES", False) or bool(
    _ROLE_APP_FEATURE_PACKS or _ROLE_APP_DISABLED_APPS
)
DIRECT_ROUTE_PROVIDER_APP_SELECTORS = ("apps.ocpp",)
PUBLIC_ROUTE_PROVIDER_APP_SELECTORS = ()
CHARGER_INTAKE_PUBLIC_ROUTES_ENABLED = "charger_intake" in {
    normalize_feature_pack_name(pack) for pack in _ROLE_APP_FEATURE_PACKS
}


def _resolve_direct_route_app_selectors(
    *,
    node_role: str,
    feature_packs: Iterable[str] = (),
) -> set[str] | None:
    try:
        normalized_role = normalize_role_profile(node_role)
    except ValueError:
        return None
    normalized_feature_packs = tuple(
        normalize_feature_pack_name(feature_pack) for feature_pack in feature_packs
    )
    if normalized_role == RoleProfile.CONTROL:
        direct_selectors = set(get_role_default_app_selectors(normalized_role))
        direct_selectors.discard("apps.ocpp")
    else:
        direct_selectors = set(ROLE_DEFAULT_APP_SELECTORS[normalized_role])
    direct_selectors.update(get_feature_pack_app_selectors(normalized_feature_packs))
    return direct_selectors


def _resolve_route_provider_disabled_apps(
    *,
    node_role: str,
    profile_enabled: bool,
    enabled_app_lock_entries: Iterable[str] | None,
    enabled_app_lock_direct_entries: Iterable[str] | None = None,
    feature_packs: Iterable[str] = (),
) -> list[str]:
    """Return route-provider apps hidden when installed only for dependencies."""

    if enabled_app_lock_entries is not None:
        validate_no_deprecated_feature_packs(feature_packs)
        normalized_lock_entries = set(
            _normalize_selected_app_entries(
                enabled_app_lock_entries,
                _dedupe_app_entries(_BUILT_IN_APP_ENTRIES),
            )
        )
        direct_profile_selectors: set[str] = set()
        if enabled_app_lock_direct_entries is not None:
            normalized_direct_entries = set(
                _normalize_selected_app_entries(
                    enabled_app_lock_direct_entries,
                    _dedupe_app_entries(_BUILT_IN_APP_ENTRIES),
                )
            )
            direct_route_entries = normalized_direct_entries & set(
                DIRECT_ROUTE_PROVIDER_APP_SELECTORS
            )
            direct_profile_selectors = direct_route_entries
        return [
            app_selector
            for app_selector in DIRECT_ROUTE_PROVIDER_APP_SELECTORS
            if app_selector not in normalized_lock_entries
            or app_selector not in direct_profile_selectors
        ]

    if not profile_enabled:
        return []

    direct_profile_selectors = _resolve_direct_route_app_selectors(
        node_role=node_role,
        feature_packs=feature_packs,
    )
    if direct_profile_selectors is None:
        return []
    return [
        app_selector
        for app_selector in DIRECT_ROUTE_PROVIDER_APP_SELECTORS
        if app_selector not in direct_profile_selectors
    ]


ROUTE_PROVIDER_DISABLED_APPS = _resolve_route_provider_disabled_apps(
    node_role=NODE_ROLE,
    profile_enabled=ROLE_APP_PROFILES_ENABLED,
    enabled_app_lock_entries=_ENABLED_APP_LOCK_ENTRIES,
    enabled_app_lock_direct_entries=_ENABLED_APP_LOCK_DIRECT_ENTRIES,
    feature_packs=_ROLE_APP_FEATURE_PACKS,
)

INSTALLED_APPS = (
    _resolve_installed_app_entries(
        node_role=NODE_ROLE,
        profile_enabled=ROLE_APP_PROFILES_ENABLED,
        enabled_app_lock_entries=_ENABLED_APP_LOCK_ENTRIES,
        feature_packs=_ROLE_APP_FEATURE_PACKS,
        disabled_apps=_ROLE_APP_DISABLED_APPS,
    )
    + ARTHEXIS_EXTERNAL_APPS
)

OPTIONAL_MIGRATION_STATE_PROVIDER_APPS = (SCREENS_APP_SELECTOR,)
DJANGO_GLOBAL_OPTIONS_WITH_VALUES = {"--settings", "--pythonpath", "--verbosity", "-v"}
PYTEST_ENTRYPOINT_NAMES = {"pytest", "pytest.exe", "py.test", "py.test.exe"}
PYTEST_ENV_MARKERS = ("PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER")


def _is_pytest_entrypoint(value: object) -> bool:
    """Return True for direct pytest binaries and python -m pytest entrypoints."""

    normalized = str(value).replace("\\", "/").lower()
    name = normalized.rsplit("/", maxsplit=1)[-1]
    return name in PYTEST_ENTRYPOINT_NAMES or normalized.endswith("/pytest/__main__.py")


def _management_command_tokens() -> tuple[str, ...]:
    """Return Django management command tokens after leading global options."""

    args = sys.argv[1:]
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            return tuple(args[index:])

        option_name = arg.split("=", maxsplit=1)[0]
        if option_name in DJANGO_GLOBAL_OPTIONS_WITH_VALUES and "=" not in arg:
            index += 2
        else:
            index += 1

    return ()


def _is_makemigrations_command() -> bool:
    """Return True when Django is building canonical migration model state."""

    command_tokens = _management_command_tokens()
    return command_tokens[:1] == ("makemigrations",) or command_tokens[:2] in {
        ("migrations", "check"),
        ("migrations", "make"),
    }


def _is_test_management_command() -> bool:
    """Return True when the suite test command needs its app registry."""

    if env_bool("ARTHEXIS_TEST_MANAGEMENT_COMMAND", False):
        return True

    if any(os.environ.get(name) for name in PYTEST_ENV_MARKERS):
        return True

    if any(_is_pytest_entrypoint(arg) for arg in sys.argv[:3]):
        return True

    if len(sys.argv) >= 3 and sys.argv[1:3] == ["-m", "pytest"]:
        return True

    command_tokens = _management_command_tokens()
    return (bool(command_tokens) and command_tokens[0] == "test") or (
        len(command_tokens) >= 2
        and command_tokens[0] == "help"
        and command_tokens[1] == "test"
    )


def _route_provider_disabled_apps_for_runtime(
    disabled_apps: Iterable[str],
) -> list[str]:
    """Keep route-provider gating out of broad suite test runs."""

    if _is_test_management_command():
        return []
    return list(disabled_apps)


def _append_migration_compatibility_apps(installed_apps: Iterable[str]) -> list[str]:
    """Load preserved optional app state while rebuilding/checking migrations."""

    resolved_apps = _dedupe_app_entries(installed_apps)
    if _is_makemigrations_command():
        return _ordered_installed_app_entries(
            _BUILT_IN_APP_ENTRIES,
            [
                *resolved_apps,
                *close_app_dependencies(OPTIONAL_MIGRATION_STATE_PROVIDER_APPS),
            ],
        )

    return resolved_apps


ROUTE_PROVIDER_DISABLED_APPS = _route_provider_disabled_apps_for_runtime(
    ROUTE_PROVIDER_DISABLED_APPS
)

if HAS_DEBUG_TOOLBAR:
    INSTALLED_APPS.append("debug_toolbar")

INSTALLED_APPS = _append_migration_compatibility_apps(INSTALLED_APPS)

if "apps.sites" not in INSTALLED_APPS:
    CSRF_FAILURE_VIEW = "django.views.csrf.csrf_failure"

SITE_ID = 1

MIGRATION_MODULES = {
    # Pin django_celery_beat migrations to a local copy so we can override
    # upstream changes that introduce optional dependencies (e.g. Google
    # Calendar profile) and avoid InvalidBases errors during migrate.
    "django_celery_beat": "apps.celery.beat_migrations",
    "sites": "apps.core.sites_migrations",
}

_original_get_current_site = sites_shortcuts.get_current_site


def _get_current_site_with_request_fallback(request=None):
    """Fallback to RequestSite during startup when Site records are unavailable."""

    from django.contrib.sites.models import Site
    from django.db.utils import OperationalError, ProgrammingError

    try:
        return _original_get_current_site(request)
    except (Site.DoesNotExist, OperationalError, ProgrammingError):
        if request is not None:
            return RequestSite(request)
        raise


sites_shortcuts.get_current_site = _get_current_site_with_request_fallback
