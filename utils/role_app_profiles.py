"""Role-based application profile declarations.

This module is intentionally independent from Django settings. The rollout can
import these declarations from settings in a later step without changing
runtime app selection in the declaration-only step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from utils.app_manifests import (
    load_app_dependency_metadata,
    load_manifest_declared_app_entries,
)

AppSelector: TypeAlias = str
FeaturePackName: TypeAlias = str

DEPRECATED_FEATURE_PACKS = frozenset({"charger_cutovers", "local_summaries"})
IMAGER_APP_SELECTOR: AppSelector = "apps.imager"
FEATURE_PACK_ONLY_APP_SELECTORS: tuple[AppSelector, ...] = ()
DIRECT_LOCK_REASON_PREFIXES = ("role-default:", "feature-pack:")
DIRECT_LOCK_REASONS = frozenset({"explicit-include", "full-app-fallback:unknown-role"})
RETIRED_RUNTIME_APP_SELECTORS = frozenset(
    {
        "apps.journals",
        "apps.logbook",
        "apps.special",
        "apps.tasks",
        "apps.tests",
        "apps.cdn",
        "apps.charger_intake",
        "apps.content",
        "apps.cutover",
        "apps.gallery",
        "apps.links",
        "apps.widgets",
        "apps.video",
    }
)
PUBLIC_COMMERCE_DIRECT_ROUTE_SELECTORS: tuple[AppSelector, ...] = ()
FULL_SUITE_DIRECT_ROUTE_SELECTORS: tuple[AppSelector, ...] = (
    *PUBLIC_COMMERCE_DIRECT_ROUTE_SELECTORS,
)


@dataclass(frozen=True)
class ResolvedAppExplanation:
    """One enabled app selector with the inputs that selected it."""

    selector: AppSelector
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedAppSet:
    """Explainable enabled app selector resolution result."""

    role: str
    role_profile: RoleProfile | None
    selectors: tuple[AppSelector, ...]
    explanations: tuple[ResolvedAppExplanation, ...]
    fallback_reason: str | None = None


class RoleProfile(str, Enum):
    """Supported node role application profiles."""

    WATCHTOWER = "watchtower"
    CONTROL = "control"
    SATELLITE = "satellite"
    TERMINAL = "terminal"


ROLE_ALIASES: Mapping[str, RoleProfile] = {
    "constellation": RoleProfile.WATCHTOWER,
    "control": RoleProfile.CONTROL,
    "satellite": RoleProfile.SATELLITE,
    "terminal": RoleProfile.TERMINAL,
    "watchtower": RoleProfile.WATCHTOWER,
}

PLATFORM_APP_SELECTORS: tuple[AppSelector, ...] = (
    "apps.whitenoise",
    "config.auth_app.AuthConfig",
    "apps.celery.beat_app.CeleryBeatConfig",
)

DJANGO_CORE_APP_SELECTORS: tuple[AppSelector, ...] = (
    "django.contrib.admin",
    "django.contrib.admindocs",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "django.contrib.sites",
    "django.contrib.staticfiles",
)

THIRD_PARTY_BASELINE_APP_SELECTORS: tuple[AppSelector, ...] = (
    "channels",
    "django_mermaid.apps.MermaidConfig",
    "django_object_actions",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "import_export",
    "parler",
)

ALL_NODE_APP_SELECTORS: tuple[AppSelector, ...] = (
    "apps.app",
    "apps.base",
    "apps.celery",
    "apps.core",
    "apps.credentials",
    "apps.counters",
    "apps.features",
    "apps.groups",
    "apps.locale",
    "apps.locals",
    "apps.media",
    "apps.modules",
    "apps.odoo",
    "apps.ocpp",
    "apps.release",
    "apps.services",
    "apps.sigils",
    "apps.sites",
    "apps.totp",
    "apps.users",
)

ROLE_DEFAULT_APP_SELECTORS: Mapping[RoleProfile, tuple[AppSelector, ...]] = {
    RoleProfile.WATCHTOWER: (
        "apps.actions",
        "apps.certs",
        "apps.docs",
        "apps.dns",
        "apps.emails",
        "apps.nginx",
        "apps.ops",
        "apps.protocols",
        "apps.reports",
        "apps.repos",
    ),
    RoleProfile.CONTROL: (
        "apps.cards",
        "apps.discovery",
        IMAGER_APP_SELECTOR,
        "apps.nmcli",
        "apps.rpiconnect",
        "apps.sensors",
        "apps.serialbridge",
    ),
    RoleProfile.SATELLITE: (
        "apps.discovery",
        "apps.nmcli",
        "apps.ocpp",
        "apps.protocols",
        "apps.sensors",
        "apps.serialbridge",
    ),
    RoleProfile.TERMINAL: (
        "apps.docs",
        IMAGER_APP_SELECTOR,
        "apps.repos",
        "apps.skills",
        "apps.terminals",
    ),
}

FEATURE_PACK_APP_SELECTORS: Mapping[FeaturePackName, tuple[AppSelector, ...]] = {
    "admin_actions": ("apps.actions",),
    "api_service_tokens": ("apps.apis",),
    "audio_collection": (),
    "browser_automation": (),
    "charger_intake": (),
    "cdn_assets": (),
    "clock_devices": ("apps.clocks",),
    "cloud_deployment": (),
    "crm_office": ("apps.odoo",),
    "device_simulation": (),
    "energy_billing": ("apps.energy",),
    "feedback_chat": (),
    "file_transfer": (),
    "hardware_experiments": (
        "apps.cards",
        "apps.sensors",
    ),
    "hosted_ocpp": (
        "apps.ocpp",
        "apps.nodes",
        "apps.cards",
        "apps.energy",
        "apps.maps",
    ),
    "image_classification": (),
    "logbook": (),
    "local_ocpp_testing": ("apps.ocpp", "apps.protocols"),
    "ocpp_experiments": ("apps.ocpp",),
    "ocpp_forwarding": (),
    "printer_workflows": ("apps.printers",),
    "public_commerce": (),
    "public_widgets": (),
    "rpi_connect": ("apps.rpiconnect",),
    "rpi_connect_updates": (IMAGER_APP_SELECTOR, "apps.rpiconnect"),
    "screen_devices": (),
    "task_management": (),
    "terms_pages": (),
}


def _filter_retired_app_dependencies(
    dependencies: Mapping[AppSelector, Iterable[AppSelector]],
) -> dict[AppSelector, tuple[AppSelector, ...]]:
    return {
        selector: tuple(
            dependency
            for dependency in dependencies
            if dependency not in RETIRED_RUNTIME_APP_SELECTORS
        )
        for selector, dependencies in dependencies.items()
        if selector not in RETIRED_RUNTIME_APP_SELECTORS
    }


PROFILE_APP_DEPENDENCIES: Mapping[AppSelector, tuple[AppSelector, ...]] = (
    _filter_retired_app_dependencies(load_app_dependency_metadata())
)

REQUIRED_APP_SELECTORS: Mapping[AppSelector, str] = {
    "apps.app": (
        "Application enablement state and admin integrations depend on the "
        "Applications app during settings startup."
    ),
    "apps.core": (
        "enabled-apps lock rendering, management commands, and bootstrap recovery "
        "paths depend on Core."
    ),
    "apps.cards": (
        "baseline admin forms, node synchronization actions, and RFID/customer "
        "relationships currently declare Cards models."
    ),
    "apps.energy": (
        "RFID/card account relationships and import/export flows currently declare "
        "Energy models."
    ),
    "apps.ocpp": (
        "RFID attempts and client energy reports currently declare OCPP charger "
        "and transaction relationships."
    ),
    "apps.odoo": (
        "Preserved CRM connector models back energy, email, and task management "
        "relationships in the 1.0 migration baseline."
    ),
    "apps.sites": (
        "django.contrib.sites migration overrides and user/group profile "
        "migrations currently depend on the Pages/Sites app."
    ),
    "apps.users": (
        "AUTH_USER_MODEL and authentication backends require the Users app during "
        "settings startup."
    ),
}


def normalize_role_profile(role: RoleProfile | str) -> RoleProfile:
    """Return a supported role profile for user/config supplied role text."""

    if isinstance(role, RoleProfile):
        return role

    normalized = str(role).strip().lower().replace("-", "_")
    try:
        return ROLE_ALIASES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(ROLE_ALIASES))
        raise ValueError(
            f"Unknown role profile {role!r}. Expected one of: {supported}"
        ) from exc


def _normalize_feature_pack_token(feature_pack: str) -> FeaturePackName:
    return str(feature_pack).strip().lower().replace("-", "_")


def validate_no_deprecated_feature_packs(feature_packs: Iterable[str]) -> None:
    """Raise when feature-pack input references a retired unsupported pack."""

    for feature_pack in feature_packs:
        normalized = _normalize_feature_pack_token(feature_pack)
        if normalized in DEPRECATED_FEATURE_PACKS:
            raise ValueError(
                f"Feature pack {feature_pack!r} is deprecated and unsupported; "
                "remove it from "
                "ARTHEXIS_ROLE_APP_FEATURE_PACKS/ARTHEXIS_FEATURE_PACKS."
            )


def normalize_feature_pack_name(feature_pack: str) -> FeaturePackName:
    """Return a normalized feature pack name."""

    normalized = _normalize_feature_pack_token(feature_pack)
    if normalized in DEPRECATED_FEATURE_PACKS:
        raise ValueError(
            f"Feature pack {feature_pack!r} is deprecated and unsupported; "
            "remove it from ARTHEXIS_ROLE_APP_FEATURE_PACKS/ARTHEXIS_FEATURE_PACKS."
        )
    if normalized not in FEATURE_PACK_APP_SELECTORS:
        supported = ", ".join(sorted(FEATURE_PACK_APP_SELECTORS))
        raise ValueError(
            f"Unknown feature pack {feature_pack!r}. Expected one of: {supported}"
        ) from exc
    return normalized


def _dedupe_selectors(app_selectors: Iterable[AppSelector]) -> tuple[AppSelector, ...]:
    deduped: list[AppSelector] = []
    seen: set[AppSelector] = set()
    for app_selector in app_selectors:
        normalized = app_selector.strip()
        if not normalized or normalized in seen:
            continue

        deduped.append(normalized)
        seen.add(normalized)

    return tuple(deduped)


def filter_retired_app_selectors(
    app_selectors: Iterable[AppSelector],
) -> tuple[AppSelector, ...]:
    """Return selectors after removing apps that are no longer runtime-installable."""

    return tuple(
        selector
        for selector in _dedupe_selectors(app_selectors)
        if selector not in RETIRED_RUNTIME_APP_SELECTORS
    )


def _control_app_selectors() -> tuple[AppSelector, ...]:
    feature_pack_only = set(FEATURE_PACK_ONLY_APP_SELECTORS)
    return tuple(
        selector
        for selector in sorted(load_manifest_declared_app_entries())
        if selector not in feature_pack_only
        and selector not in RETIRED_RUNTIME_APP_SELECTORS
    )


def _role_profile_default_app_selectors(
    role_profile: RoleProfile,
) -> tuple[AppSelector, ...]:
    if role_profile is RoleProfile.CONTROL:
        return _dedupe_selectors(
            (
                *ROLE_DEFAULT_APP_SELECTORS[role_profile],
                *_control_app_selectors(),
            )
        )
    return ROLE_DEFAULT_APP_SELECTORS[role_profile]


def _add_reason(
    reasons: dict[AppSelector, list[str]], app_selector: AppSelector, reason: str
) -> None:
    normalized = app_selector.strip()
    if not normalized:
        return

    selector_reasons = reasons.setdefault(normalized, [])
    if reason not in selector_reasons:
        selector_reasons.append(reason)


def _disabled_selector_aliases(app_selector: AppSelector) -> tuple[str, ...]:
    normalized = app_selector.strip()
    aliases = [normalized]
    if normalized.startswith("apps."):
        aliases.append(normalized.removeprefix("apps."))
        aliases.append(normalized.rsplit(".", maxsplit=1)[-1])
    elif "." not in normalized:
        aliases.append(f"apps.{normalized}")
    return tuple(_dedupe_selectors(aliases))


def filter_disabled_app_selectors(
    app_selectors: Iterable[AppSelector],
    disabled_apps: Iterable[AppSelector] = (),
) -> tuple[AppSelector, ...]:
    """Return app selectors after applying explicit local disables."""

    disabled = {
        alias
        for disabled_app in _dedupe_selectors(disabled_apps)
        for alias in _disabled_selector_aliases(disabled_app)
    }
    if not disabled:
        return _dedupe_selectors(app_selectors)

    return tuple(
        app_selector
        for app_selector in _dedupe_selectors(app_selectors)
        if not any(
            alias in disabled for alias in _disabled_selector_aliases(app_selector)
        )
    )


def validate_required_app_selectors_not_disabled(
    disabled_apps: Iterable[AppSelector],
    *,
    required_apps: Mapping[AppSelector, str] = REQUIRED_APP_SELECTORS,
) -> None:
    """Raise a clear error when a disabled selector targets a required app."""

    disabled = {
        alias
        for disabled_app in _dedupe_selectors(disabled_apps)
        for alias in _disabled_selector_aliases(disabled_app)
    }
    for app_selector, reason in required_apps.items():
        if any(alias in disabled for alias in _disabled_selector_aliases(app_selector)):
            raise ValueError(f"{app_selector} cannot be disabled because {reason}")


def validate_required_app_selectors_available(
    app_selectors: Iterable[AppSelector],
    *,
    required_apps: Mapping[AppSelector, str] = REQUIRED_APP_SELECTORS,
) -> None:
    """Raise a clear error when dependency pruning removes a required app."""

    selected = {
        alias
        for app_selector in _dedupe_selectors(app_selectors)
        for alias in _disabled_selector_aliases(app_selector)
    }
    for app_selector, reason in required_apps.items():
        if not any(
            alias in selected for alias in _disabled_selector_aliases(app_selector)
        ):
            raise ValueError(f"{app_selector} cannot be omitted because {reason}")


def close_app_dependencies(
    app_selectors: Iterable[AppSelector],
    *,
    dependencies: Mapping[
        AppSelector, Iterable[AppSelector]
    ] = PROFILE_APP_DEPENDENCIES,
) -> tuple[AppSelector, ...]:
    """Return app selectors plus transitive dependencies, preserving first use."""

    closed = list(filter_retired_app_selectors(app_selectors))
    seen = set(closed)
    index = 0
    while index < len(closed):
        app_selector = closed[index]
        for dependency in dependencies.get(app_selector, ()):
            normalized = dependency.strip()
            if normalized in RETIRED_RUNTIME_APP_SELECTORS:
                continue
            if normalized and normalized not in seen:
                closed.append(normalized)
                seen.add(normalized)
        index += 1

    return tuple(closed)


def _close_app_dependencies_with_reasons(
    app_selectors: Iterable[AppSelector],
    reasons: dict[AppSelector, list[str]],
    *,
    dependencies: Mapping[
        AppSelector, Iterable[AppSelector]
    ] = PROFILE_APP_DEPENDENCIES,
) -> tuple[AppSelector, ...]:
    """Close dependencies while recording the parent that pulled them in."""

    closed = list(filter_retired_app_selectors(app_selectors))
    seen = set(closed)
    index = 0
    while index < len(closed):
        app_selector = closed[index]
        for dependency in dependencies.get(app_selector, ()):
            normalized = dependency.strip()
            if not normalized:
                continue
            if normalized in RETIRED_RUNTIME_APP_SELECTORS:
                continue
            _add_reason(reasons, normalized, f"dependency-closure:{app_selector}")
            if normalized not in seen:
                closed.append(normalized)
                seen.add(normalized)
        index += 1

    return tuple(closed)


def filter_app_selectors_with_available_dependencies(
    app_selectors: Iterable[AppSelector],
    *,
    dependencies: Mapping[
        AppSelector, Iterable[AppSelector]
    ] = PROFILE_APP_DEPENDENCIES,
) -> tuple[AppSelector, ...]:
    """Remove selectors whose declared required dependencies are unavailable."""

    selected = list(filter_retired_app_selectors(app_selectors))
    selected_set = set(selected)
    changed = True
    while changed:
        changed = False
        retained: list[AppSelector] = []
        for app_selector in selected:
            missing_dependency = any(
                dependency.strip() and dependency.strip() not in selected_set
                for dependency in dependencies.get(app_selector, ())
            )
            if missing_dependency:
                selected_set.remove(app_selector)
                changed = True
                continue
            retained.append(app_selector)
        selected = retained

    return tuple(selected)


def get_feature_pack_app_selectors(
    feature_packs: Iterable[str],
) -> tuple[AppSelector, ...]:
    """Return app selectors declared by optional feature packs."""

    app_selectors: list[AppSelector] = []
    for feature_pack in feature_packs:
        app_selectors.extend(
            FEATURE_PACK_APP_SELECTORS[normalize_feature_pack_name(feature_pack)]
        )
    return filter_retired_app_selectors(app_selectors)


def get_role_default_app_selectors(role: RoleProfile | str) -> tuple[AppSelector, ...]:
    """Return the all-node baseline plus role-specific default selectors."""

    role_profile = normalize_role_profile(role)
    return _dedupe_selectors(
        (
            *PLATFORM_APP_SELECTORS,
            *DJANGO_CORE_APP_SELECTORS,
            *THIRD_PARTY_BASELINE_APP_SELECTORS,
            *ALL_NODE_APP_SELECTORS,
            *_role_profile_default_app_selectors(role_profile),
        )
    )


def resolve_role_app_selectors(
    role: RoleProfile | str,
    *,
    feature_packs: Iterable[str] = (),
    disabled_apps: Iterable[AppSelector] = (),
    required_apps: Mapping[AppSelector, str] = REQUIRED_APP_SELECTORS,
    dependencies: Mapping[
        AppSelector, Iterable[AppSelector]
    ] = PROFILE_APP_DEPENDENCIES,
) -> tuple[AppSelector, ...]:
    """Return app selectors for a role plus explicit feature packs and closure."""

    validate_required_app_selectors_not_disabled(
        disabled_apps,
        required_apps=required_apps,
    )
    initial = filter_retired_app_selectors(
        filter_disabled_app_selectors(
            (
                *get_role_default_app_selectors(role),
                *get_feature_pack_app_selectors(feature_packs),
            ),
            disabled_apps,
        )
    )
    closed = close_app_dependencies(
        initial,
        dependencies=dependencies,
    )
    selected = filter_disabled_app_selectors(closed, disabled_apps)
    selectors = filter_app_selectors_with_available_dependencies(
        selected,
        dependencies=dependencies,
    )
    validate_required_app_selectors_available(selectors, required_apps=required_apps)
    return selectors


def get_direct_lock_app_selectors(result: ResolvedAppSet) -> tuple[AppSelector, ...]:
    """Return selectors that generated locks should preserve as direct intent."""

    direct_selectors = [
        item.selector
        for item in result.explanations
        if any(
            reason in DIRECT_LOCK_REASONS
            or reason.startswith(DIRECT_LOCK_REASON_PREFIXES)
            for reason in item.reasons
        )
        and not (
            result.role_profile is RoleProfile.CONTROL
            and item.selector == "apps.ocpp"
            and not any(
                reason == "explicit-include" or reason.startswith("feature-pack:")
                for reason in item.reasons
            )
        )
    ]
    result_selectors = set(result.selectors)
    public_commerce_enabled = any(
        item.selector == "apps.shop" and "feature-pack:public_commerce" in item.reasons
        for item in result.explanations
    )
    if public_commerce_enabled:
        direct_selectors.extend(
            selector
            for selector in PUBLIC_COMMERCE_DIRECT_ROUTE_SELECTORS
            if selector in result_selectors
        )
    return _dedupe_selectors(direct_selectors)


def _direct_lock_source_for_reasons(reasons: tuple[str, ...]) -> str | None:
    if "explicit-include" in reasons:
        return None
    for reason in reasons:
        if reason.startswith("feature-pack:"):
            return reason
    for reason in reasons:
        if reason.startswith("role-default:") or reason.startswith(
            "full-app-fallback:"
        ):
            return reason
    return None


def get_direct_lock_app_sources(result: ResolvedAppSet) -> dict[str, str]:
    """Return generated source labels for direct lock selectors."""

    direct_selectors = set(get_direct_lock_app_selectors(result))
    explicit_direct_selectors = {
        item.selector
        for item in result.explanations
        if item.selector in direct_selectors and "explicit-include" in item.reasons
    }
    sources = {
        item.selector: source
        for item in result.explanations
        if item.selector in direct_selectors
        if (source := _direct_lock_source_for_reasons(item.reasons))
    }
    public_commerce_enabled = any(
        item.selector == "apps.shop" and "feature-pack:public_commerce" in item.reasons
        for item in result.explanations
    )
    if public_commerce_enabled:
        sources.update(
            {
                selector: "feature-pack:public_commerce"
                for selector in PUBLIC_COMMERCE_DIRECT_ROUTE_SELECTORS
                if selector in direct_selectors
                and selector not in sources
                and selector not in explicit_direct_selectors
            }
        )
    return sources


def explain_role_app_selectors(
    role: RoleProfile | str,
    *,
    feature_packs: Iterable[str] = (),
    explicit_apps: Iterable[AppSelector] = (),
    disabled_apps: Iterable[AppSelector] = (),
    required_apps: Mapping[AppSelector, str] = REQUIRED_APP_SELECTORS,
    dependencies: Mapping[
        AppSelector, Iterable[AppSelector]
    ] = PROFILE_APP_DEPENDENCIES,
    fallback_app_selectors: Iterable[AppSelector] | None = None,
) -> ResolvedAppSet:
    """Resolve app selectors and explain which profile input selected each app.

    ``fallback_app_selectors`` preserves setup and recovery behavior when the
    node role is not known yet. When supplied and the role is unknown, the
    result keeps the provided full app set instead of raising.
    """

    role_text = role.value if isinstance(role, RoleProfile) else str(role)
    feature_pack_values = tuple(feature_packs)
    validate_required_app_selectors_not_disabled(
        disabled_apps,
        required_apps=required_apps,
    )
    validate_no_deprecated_feature_packs(feature_pack_values)
    reasons: dict[AppSelector, list[str]] = {}

    try:
        role_profile = normalize_role_profile(role)
    except ValueError:
        if fallback_app_selectors is None:
            raise
        selected = filter_retired_app_selectors(
            filter_disabled_app_selectors(
                _dedupe_selectors(fallback_app_selectors),
                disabled_apps,
            )
        )
        selectors = filter_app_selectors_with_available_dependencies(
            selected,
            dependencies=dependencies,
        )
        validate_required_app_selectors_available(
            selectors,
            required_apps=required_apps,
        )
        for selector in selectors:
            _add_reason(reasons, selector, "full-app-fallback:unknown-role")
        return ResolvedAppSet(
            role=role_text,
            role_profile=None,
            selectors=selectors,
            explanations=tuple(
                ResolvedAppExplanation(selector, tuple(reasons[selector]))
                for selector in selectors
            ),
            fallback_reason=f"unknown role profile: {role_text}",
        )

    normalized_feature_packs = tuple(
        normalize_feature_pack_name(feature_pack)
        for feature_pack in feature_pack_values
    )
    for selector in (
        *PLATFORM_APP_SELECTORS,
        *DJANGO_CORE_APP_SELECTORS,
        *THIRD_PARTY_BASELINE_APP_SELECTORS,
        *ALL_NODE_APP_SELECTORS,
    ):
        _add_reason(reasons, selector, "all-node")
    for selector in _role_profile_default_app_selectors(role_profile):
        _add_reason(reasons, selector, f"role-default:{role_profile.value}")

    for feature_pack in normalized_feature_packs:
        for selector in FEATURE_PACK_APP_SELECTORS[feature_pack]:
            _add_reason(reasons, selector, f"feature-pack:{feature_pack}")

    for selector in explicit_apps:
        _add_reason(reasons, selector, "explicit-include")

    initial = filter_retired_app_selectors(
        filter_disabled_app_selectors(
            (
                *get_role_default_app_selectors(role_profile),
                *get_feature_pack_app_selectors(normalized_feature_packs),
                *_dedupe_selectors(explicit_apps),
            ),
            disabled_apps,
        )
    )
    closed = _close_app_dependencies_with_reasons(
        initial,
        reasons,
        dependencies=dependencies,
    )
    selected = filter_disabled_app_selectors(closed, disabled_apps)
    selectors = filter_app_selectors_with_available_dependencies(
        selected,
        dependencies=dependencies,
    )
    validate_required_app_selectors_available(selectors, required_apps=required_apps)

    return ResolvedAppSet(
        role=role_text,
        role_profile=role_profile,
        selectors=selectors,
        explanations=tuple(
            ResolvedAppExplanation(selector, tuple(reasons.get(selector, ())))
            for selector in selectors
        ),
    )