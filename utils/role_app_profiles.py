"""Role-based application profile declarations.

This module is intentionally independent from Django settings. The rollout can
import these declarations from settings in a later step without changing
runtime app selection in the declaration-only step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
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
        "apps.apis",
        "apps.journals",
        "apps.logbook",
        "apps.special",
        "apps.tasks",
        "apps.tests",
        "apps.cdn",
        "apps.charger_intake",
        "apps.content",
        "apps.cutover",
        "apps.dns",
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


class RoleProfile(StrEnum):
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
        IMAGER_APP_SELECTOR,
        "apps.repos",
        "apps.skills",
        "apps.terminals",
    ),
}

FEATURE_PACK_APP_SELECTORS: Mapping[FeaturePackName, tuple[AppSelector, ...]] = {
    "admin_actions": ("apps.actions",),
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

