"""Tests for role-based application profile declarations."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from django.conf import settings

from apps.core.checks.apps_registry import (
    APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID,
    get_apps_registry_configuration_errors,
)
from config.route_providers import autodiscovered_websocket_urlpatterns
from config.settings.apps import (
    _is_makemigrations_command,
    _is_test_management_command,
    _resolve_installed_app_entries,
    _resolve_route_provider_disabled_apps,
    _route_provider_disabled_apps_for_runtime,
    _split_setting_list,
)
from config.settings.celery import _resolve_celery_beat_schedule
from config.settings.middleware import _resolve_middleware_entries
from utils.app_manifests import (
    load_app_dependency_metadata,
    load_manifest_app_entries,
    load_manifest_declared_app_entries,
)
from utils.role_app_profiles import (
    ALL_NODE_APP_SELECTORS,
    FEATURE_PACK_APP_SELECTORS,
    FEATURE_PACK_ONLY_APP_SELECTORS,
    PLATFORM_APP_SELECTORS,
    PROFILE_APP_DEPENDENCIES,
    RETIRED_RUNTIME_APP_SELECTORS,
    ROLE_DEFAULT_APP_SELECTORS,
    RoleProfile,
    close_app_dependencies,
    explain_role_app_selectors,
    filter_disabled_app_selectors,
    get_direct_lock_app_selectors,
    get_feature_pack_app_selectors,
    get_role_default_app_selectors,
    normalize_role_profile,
    resolve_role_app_selectors,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
STARTUP_CHECK_TIMEOUT = 120
MANIFEST_COMPANION_APP_SELECTORS = {
    "apps.celery.beat_app.CeleryBeatConfig",
}
EXPECTED_PROFILE_APP_DEPENDENCIES = {
    "apps.celery": ("apps.celery.beat_app.CeleryBeatConfig",),
    "apps.clocks": ("apps.discovery",),
    "apps.core": ("apps.discovery", "apps.emails"),
    "apps.dns": ("apps.nmcli",),
    "apps.energy": ("apps.cards", "apps.maps"),
    "apps.maps": ("apps.energy",),
    "apps.modules": ("apps.groups", "apps.media", "apps.nodes"),
    "apps.nmcli": ("apps.discovery",),
    "apps.nodes": (
        "apps.credentials",
        "apps.discovery",
    ),
    "apps.ocpp": (
        "apps.nodes",
        "apps.cards",
        "apps.energy",
        "apps.maps",
        "apps.protocols",
    ),
    "apps.odoo": ("apps.discovery",),
    "apps.screens": ("apps.sensors", "apps.summary"),
    "apps.sites": ("apps.docs", "apps.modules"),
}


def _middleware_for_profile(
    node_role: str,
    *,
    disabled_apps: tuple[str, ...] = (),
) -> list[str]:
    installed_apps = _resolve_installed_app_entries(
        node_role=node_role,
        profile_enabled=True,
        enabled_app_lock_entries=None,
        disabled_apps=disabled_apps,
    )
    return _resolve_middleware_entries(installed_apps=installed_apps)


def _beat_schedule_for_profile(
    node_role: str,
    *,
    feature_packs: tuple[str, ...] = (),
    disabled_apps: tuple[str, ...] = (),
    profile_enabled: bool = True,
) -> dict[str, dict[str, object]]:
    installed_apps = _resolve_installed_app_entries(
        node_role=node_role,
        profile_enabled=profile_enabled,
        enabled_app_lock_entries=None,
        feature_packs=feature_packs,
        disabled_apps=disabled_apps,
    )
    return _resolve_celery_beat_schedule(installed_apps=installed_apps)


def test_role_profile_defaults_keep_optional_packs_separate_from_required_ocpp():
    watchtower_apps = set(get_role_default_app_selectors(RoleProfile.WATCHTOWER))

    assert "apps.ocpp" in watchtower_apps
    assert "apps.cards" not in watchtower_apps


def test_control_profile_defaults_to_manifest_apps_except_feature_pack_only():
    manifest_entries = load_manifest_declared_app_entries()
    control_apps = set(resolve_role_app_selectors("Control"))

    missing = sorted(
        manifest_entries
        - set(FEATURE_PACK_ONLY_APP_SELECTORS)
        - RETIRED_RUNTIME_APP_SELECTORS
        - control_apps
    )

    assert missing == []
    assert RETIRED_RUNTIME_APP_SELECTORS.isdisjoint(control_apps)
    assert "apps.screens" in control_apps
    assert "apps.shop" not in control_apps


def test_shop_is_feature_pack_only_for_role_profiles():
    for role_profile in RoleProfile:
        assert "apps.shop" not in resolve_role_app_selectors(role_profile)


def test_control_all_apps_does_not_expand_other_role_defaults():
    for role_profile in (
        RoleProfile.WATCHTOWER,
        RoleProfile.SATELLITE,
        RoleProfile.TERMINAL,
    ):
        role_apps = set(resolve_role_app_selectors(role_profile))

        assert "apps.printers" not in role_apps
        assert "apps.screens" not in role_apps


def test_satellite_profile_makes_ocpp_monitoring_explicit_without_commerce_bloat():
    result = explain_role_app_selectors("Satellite")
    apps = set(result.selectors)
    reasons = {item.selector: item.reasons for item in result.explanations}

    assert "role-default:satellite" in reasons["apps.ocpp"]
    assert "apps.cards" in apps
    assert "apps.energy" in apps
    assert "apps.maps" in apps
    assert "apps.ftp" not in apps
    assert "apps.odoo" in apps
    assert "apps.rates" not in apps
    assert "apps.repos" not in apps
    assert "apps.screens" not in apps
    assert "apps.sites" in apps
    assert "apps.shop" not in apps
    assert RETIRED_RUNTIME_APP_SELECTORS.isdisjoint(apps)


def test_resolved_profile_includes_platform_and_role_baseline():
    terminal_apps = resolve_role_app_selectors("Terminal")

    assert "config.auth_app.AuthConfig" in terminal_apps
    assert "apps.celery" in terminal_apps
    assert "apps.celery.beat_app.CeleryBeatConfig" in terminal_apps
    assert "apps.cards" in terminal_apps
    assert "apps.emails" in terminal_apps
    assert "apps.energy" in terminal_apps
    assert "apps.imager" in terminal_apps
    assert "apps.maps" in terminal_apps
    assert "apps.repos" in terminal_apps
    assert "apps.skills" in terminal_apps
    assert "apps.odoo" in terminal_apps
    assert "apps.terminals" in terminal_apps


def test_feature_packs_are_explicit_opt_ins():
    watchtower_apps = set(
        resolve_role_app_selectors(
            "watchtower",
            feature_packs=("hosted-ocpp",),
        )
    )

    assert "apps.ocpp" in watchtower_apps
    assert "apps.nodes" in watchtower_apps
    assert "apps.cards" in watchtower_apps
    assert "apps.energy" in watchtower_apps
    assert "apps.maps" in watchtower_apps


def test_api_service_tokens_feature_pack_keeps_apis_explicit():
    satellite_apps = set(
        resolve_role_app_selectors("satellite", feature_packs=("hosted_ocpp",))
    )
    api_apps = set(
        resolve_role_app_selectors(
            "satellite",
            feature_packs=("hosted_ocpp", "api_service_tokens"),
        )
    )
    control_apps = set(resolve_role_app_selectors("control"))

    assert "apps.apis" not in satellite_apps
    assert "apps.apis" in api_apps
    assert "apps.apis" in control_apps


def test_admin_actions_feature_pack_keeps_actions_explicit():
    satellite_apps = set(
        resolve_role_app_selectors("satellite", feature_packs=("hosted_ocpp",))
    )
    action_apps = set(
        resolve_role_app_selectors(
            "satellite",
            feature_packs=("hosted_ocpp", "admin_actions"),
        )
    )
    control_apps = set(resolve_role_app_selectors("control"))

    assert "apps.actions" not in satellite_apps
    assert "apps.actions" in action_apps
    assert "apps.actions" in control_apps


def test_watchtower_profile_keeps_admin_actions_for_staff_tasks_template():
    watchtower_apps = set(resolve_role_app_selectors("watchtower"))

    assert "apps.actions" in watchtower_apps


def test_screen_devices_feature_pack_enables_lcd_profile_stack():
    terminal_apps = set(resolve_role_app_selectors("terminal"))
    screen_apps = set(
        resolve_role_app_selectors(
            "terminal",
            feature_packs=("screen_devices",),
        )
    )
    hardware_apps = set(
        resolve_role_app_selectors(
            "terminal",
            feature_packs=("hardware_experiments",),
        )
    )

    assert "apps.screens" not in terminal_apps
    assert "apps.screens" in screen_apps
    assert "apps.sensors" in screen_apps
    assert "apps.summary" in screen_apps
    assert "apps.screens" in hardware_apps


def test_rpi_connect_updates_feature_pack_enables_native_artifact_builder():
    watchtower_apps = set(
        resolve_role_app_selectors("watchtower", feature_packs=("rpi-connect-updates",))
    )

    assert "apps.imager" in watchtower_apps
    assert "apps.rpiconnect" in watchtower_apps


def test_explain_role_app_selectors_reports_selection_reasons():
    result = explain_role_app_selectors(
        "terminal",
        explicit_apps=("apps.repos",),
        dependencies={"apps.repos": ("apps.discovery",)},
        required_apps={},
    )
    reasons = {item.selector: item.reasons for item in result.explanations}

    assert "all-node" in reasons["apps.core"]
    assert "role-default:terminal" in reasons["apps.imager"]
    assert "explicit-include" in reasons["apps.repos"]
    assert "dependency-closure:apps.repos" in reasons["apps.discovery"]


def test_explain_role_app_selectors_preserves_unknown_role_full_app_fallback():
    result = explain_role_app_selectors(
        "setup-recovery",
        fallback_app_selectors=("apps.repos", "apps.docs"),
        required_apps={},
    )

    assert result.role_profile is None
    assert result.fallback_reason == "unknown role profile: setup-recovery"
    assert result.selectors == ("apps.repos", "apps.docs")
    assert result.explanations[0].reasons == ("full-app-fallback:unknown-role",)


@pytest.mark.parametrize(
    "feature_pack",
    ("noep", "unknown-pack"),
)
def test_explain_role_app_selectors_falls_back_before_feature_pack_validation(
    feature_pack,
):
    result = explain_role_app_selectors(
        "setup-recovery",
        feature_packs=(feature_pack,),
        fallback_app_selectors=("apps.repos",),
        required_apps={},
    )

    assert result.role_profile is None
    assert result.fallback_reason == "unknown role profile: setup-recovery"
    assert result.selectors == ("apps.repos",)


@pytest.mark.parametrize("feature_pack", ("charger-cutovers", "charger_cutovers"))
def test_explain_role_app_selectors_rejects_deprecated_pack_before_fallback(
    feature_pack,
):
    with pytest.raises(ValueError, match="deprecated and unsupported"):
        explain_role_app_selectors(
            "setup-recovery",
            feature_packs=(feature_pack,),
            fallback_app_selectors=("apps.repos",),
            required_apps={},
        )


def test_explain_role_app_selectors_keeps_docs_when_repos_is_disabled():
    result = explain_role_app_selectors(
        "setup-recovery",
        disabled_apps=("apps.repos",),
        fallback_app_selectors=("apps.docs", "apps.repos"),
        required_apps={},
    )

    assert result.selectors == ("apps.docs",)
    assert result.explanations[0].selector == "apps.docs"
    assert result.explanations[0].reasons == ("full-app-fallback:unknown-role",)


def test_feature_pack_selectors_are_deduped():
    apps = get_feature_pack_app_selectors(("admin-actions", "admin_actions"))

    assert apps == ("apps.actions",)


def test_dependency_closure_is_transitive_and_deduped():
    apps = close_app_dependencies(
        ("apps.leaf", "apps.root"),
        dependencies={
            "apps.root": ("apps.middle", "apps.leaf"),
            "apps.middle": ("apps.leaf", "apps.shared"),
        },
    )

    assert apps == ("apps.leaf", "apps.root", "apps.middle", "apps.shared")


def test_profile_dependency_metadata_is_loaded_from_app_manifests():
    dependencies = {
        selector: tuple(
            dependency
            for dependency in dependencies
            if dependency not in RETIRED_RUNTIME_APP_SELECTORS
        )
        for selector, dependencies in load_app_dependency_metadata().items()
        if selector not in RETIRED_RUNTIME_APP_SELECTORS
    }

    assert dependencies == EXPECTED_PROFILE_APP_DEPENDENCIES
    assert PROFILE_APP_DEPENDENCIES == EXPECTED_PROFILE_APP_DEPENDENCIES


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (["manage.py", "test", "run"], True),
        (["manage.py", "help", "test"], True),
        (["manage.py", "--settings=config.settings.test", "test"], True),
        (["manage.py", "--settings", "config.settings.test", "help", "test"], True),
        (["manage.py", "--pythonpath", "/srv/app", "-v", "2", "test"], True),
        (["manage.py", "migrations", "check"], False),
        (["manage.py", "migrations", "run"], False),
        (["manage.py", "help", "migrations"], False),
        (["manage.py", "--settings=config.settings.test", "migrations", "check"], False),
        (["manage.py", "check"], False),
        (["manage.py", "--skip-checks", "showmigrations"], False),
    ),
)
def test_test_management_command_detection_skips_django_global_options(
    monkeypatch, argv, expected
):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("ARTHEXIS_TEST_MANAGEMENT_COMMAND", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    assert _is_test_management_command() is expected


@pytest.mark.parametrize(
    ("argv", "expected"),
    (
        (["manage.py", "makemigrations"], True),
        (["manage.py", "migrations", "check"], True),
        (["manage.py", "migrations", "make"], True),
        (["manage.py", "migrations", "run"], False),
        (["manage.py", "help", "migrations"], False),
        (["manage.py", "test", "run"], False),
    ),
)
def test_makemigrations_detection_uses_native_command_only(
    monkeypatch, argv, expected
):
    monkeypatch.setattr(sys, "argv", argv)

    assert _is_makemigrations_command() is expected


def test_test_management_command_detection_honors_pytest_child_marker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["python", "-m", "pytest"])
    monkeypatch.setenv("ARTHEXIS_TEST_MANAGEMENT_COMMAND", "true")

    assert _is_test_management_command() is True


@pytest.mark.parametrize(
    "argv",
    (
        ["python", "-m", "pytest"],
        [
            "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pytest/__main__.py"
        ],
        ["pytest", "apps/ocpp/tests"],
        ["pytest.exe", "apps/ocpp/tests"],
    ),
)
def test_test_management_command_detection_includes_pytest_invocations(
    monkeypatch, argv
):
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv("ARTHEXIS_TEST_MANAGEMENT_COMMAND", raising=False)

    assert _is_test_management_command() is True


def test_test_management_command_keeps_route_provider_apps_available(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "/opt/hostedtoolcache/Python/3.12.13/x64/lib/python3.12/site-packages/pytest/__main__.py",
            "apps/energy/tests",
        ],
    )
    monkeypatch.delenv("ARTHEXIS_TEST_MANAGEMENT_COMMAND", raising=False)

    assert _route_provider_disabled_apps_for_runtime(["apps.ocpp"]) == []


def test_test_management_command_honors_pytest_xdist_worker_marker(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["python", "-c", "from execnet import serve"])
    monkeypatch.delenv("ARTHEXIS_TEST_MANAGEMENT_COMMAND", raising=False)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")

    assert _is_test_management_command() is True
    assert _route_provider_disabled_apps_for_runtime(["apps.ocpp"]) == []


def test_repos_dependency_closure_does_not_pull_operator_framework_apps():
    apps = set(close_app_dependencies(("apps.repos",)))

    assert "apps.repos" in apps
    assert "apps.skills" not in apps
    assert "apps.shop" not in apps


def test_enabled_app_lock_repos_keeps_operator_framework_apps_out():
    apps = _resolve_installed_app_entries(
        node_role="Satellite",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.repos",),
    )

    assert "apps.repos" in apps
    assert "apps.skills" not in apps
    assert "apps.shop" not in apps


def test_manifest_loader_rejects_duplicate_django_app_selectors(tmp_path):
    first_manifest = tmp_path / "apps" / "first" / "manifest.py"
    second_manifest = tmp_path / "apps" / "second" / "manifest.py"
    first_manifest.parent.mkdir(parents=True)
    second_manifest.parent.mkdir(parents=True)
    first_manifest.write_text(
        'DJANGO_APPS = ["apps.duplicate"]\n',
        encoding="utf-8",
    )
    second_manifest.write_text(
        'DJANGO_APPS = ["apps.duplicate"]\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as exc_info:
        load_manifest_app_entries(tmp_path)

    message = str(exc_info.value)
    assert "apps.duplicate" in message
    assert str(first_manifest) in message
    assert str(second_manifest) in message


def test_profile_managed_local_apps_have_manifest_entries():
    manifest_entries = load_manifest_declared_app_entries()
    profile_managed = {
        selector
        for selector in (
            *(
                selector
                for role_profile in RoleProfile
                for selector in get_role_default_app_selectors(role_profile)
            ),
            *(
                selector
                for selectors in ROLE_DEFAULT_APP_SELECTORS.values()
                for selector in selectors
            ),
            *(
                selector
                for selectors in FEATURE_PACK_APP_SELECTORS.values()
                for selector in selectors
            ),
            *PROFILE_APP_DEPENDENCIES,
            *(
                selector
                for selectors in PROFILE_APP_DEPENDENCIES.values()
                for selector in selectors
            ),
        )
        if selector.startswith("apps.")
    }

    missing = sorted(
        profile_managed - manifest_entries - MANIFEST_COMPANION_APP_SELECTORS
    )

    assert missing == []


def test_disabled_selectors_match_local_app_aliases():
    apps = filter_disabled_app_selectors(
        ("apps.repos", "apps.synthetic", "apps.shop"),
        ("repos", "apps.synthetic"),
    )

    assert apps == ("apps.shop",)


def test_disabled_short_alias_only_matches_local_apps():
    apps = filter_disabled_app_selectors(
        ("django.contrib.sites", "apps.sites"),
        ("sites",),
    )

    assert apps == ("django.contrib.sites",)


def test_profile_dependency_closure_preserves_explicit_disables():
    apps = resolve_role_app_selectors(
        "watchtower",
        disabled_apps=("apps.synthetic_dependency",),
        dependencies={"apps.repos": ("apps.synthetic_dependency",)},
        required_apps={},
    )

    assert "apps.synthetic_dependency" not in apps
    assert "apps.repos" not in apps


def test_profile_dependency_pruning_removes_dependents_of_disabled_apps():
    apps = resolve_role_app_selectors(
        "terminal",
        disabled_apps=("apps.synthetic_dependency",),
        dependencies={"apps.repos": ("apps.synthetic_dependency",)},
        required_apps={},
    )

    assert "apps.synthetic_dependency" not in apps
    assert "apps.repos" not in apps


def test_profile_dependency_pruning_rejects_missing_required_apps():
    with pytest.raises(ValueError, match="apps.core cannot be omitted"):
        resolve_role_app_selectors("terminal", disabled_apps=("discovery",))


def test_profile_closure_includes_current_modules_dependency_chain():
    watchtower_apps = set(resolve_role_app_selectors("watchtower"))

    assert "apps.modules" in watchtower_apps
    assert "apps.nodes" in watchtower_apps
    assert "apps.credentials" in watchtower_apps
    assert "apps.discovery" in watchtower_apps


def test_control_profile_keeps_expected_runtime_apps():
    control_apps = set(resolve_role_app_selectors("control"))

    assert "apps.locals" in control_apps
    assert "apps.skills" in control_apps
    assert "apps.shop" not in control_apps
    assert RETIRED_RUNTIME_APP_SELECTORS.isdisjoint(control_apps)


def test_profile_route_providers_hide_dependency_only_route_apps():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_hide_route_apps_when_lock_omits_them():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Terminal",
        profile_enabled=True,
        enabled_app_lock_entries=("apps.cards", "apps.core"),
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_hide_route_apps_when_profiles_are_off():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Terminal",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.cards", "apps.core"),
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_metadata_less_lock_route_providers_fail_closed_for_public_routes():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=(
            "apps.ops",
            "apps.shop",
        ),
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_enable_public_commerce_dependencies():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.shop",),
        enabled_app_lock_direct_entries=("apps.shop",),
        feature_packs=("public-commerce",),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_honor_direct_public_route_metadata():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Terminal",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.shop",),
        enabled_app_lock_direct_entries=("apps.shop",),
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_do_not_close_direct_metadata_through_dependencies():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Satellite",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.ocpp", "apps.cards"),
        enabled_app_lock_direct_entries=("apps.ocpp",),
        feature_packs=(),
    )

    assert "apps.ocpp" not in disabled_route_apps


def test_public_commerce_feature_pack_keeps_route_apps_hidden():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("public-commerce",),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_control_profile_hides_shop_routes_without_public_commerce():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Control",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_hosted_ocpp_feature_pack_enables_ocpp_route_provider():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("hosted-ocpp",),
    )

    assert disabled_route_apps == []


def test_charger_intake_feature_pack_is_retired():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("charger-intake",),
    )

    result = explain_role_app_selectors(
        "Watchtower",
        feature_packs=("charger-intake",),
    )
    direct_selectors = get_direct_lock_app_selectors(result)

    assert disabled_route_apps == ["apps.ocpp"]
    assert "apps.ocpp" in result.selectors
    assert "apps.ocpp" not in direct_selectors

def test_disabled_ocpp_route_provider_hides_websocket_patterns(monkeypatch):
    monkeypatch.setattr(settings, "ASGI_ROUTE_PROVIDERS", ["apps.ocpp.routing"])
    monkeypatch.setattr(settings, "ROUTE_PROVIDER_DISABLED_APPS", ["apps.ocpp"])

    assert autodiscovered_websocket_urlpatterns() == []


def test_lock_route_providers_do_not_treat_all_node_ocpp_as_route_direct():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Terminal",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.ocpp",),
        enabled_app_lock_direct_entries=(),
        feature_packs=(),
    )

    assert disabled_route_apps == ["apps.ocpp"]


def test_lock_route_providers_honor_ocpp_route_direct_metadata():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.ocpp",),
        enabled_app_lock_direct_entries=("apps.ocpp",),
        feature_packs=(),
    )

    assert "apps.ocpp" not in disabled_route_apps


def test_direct_lock_selectors_exclude_control_role_default_ocpp():
    result = explain_role_app_selectors("Control")

    direct_selectors = get_direct_lock_app_selectors(result)

    assert "apps.ocpp" not in direct_selectors


def test_direct_lock_selectors_keep_control_feature_pack_ocpp():
    result = explain_role_app_selectors("Control", feature_packs=("hosted-ocpp",))

    direct_selectors = get_direct_lock_app_selectors(result)

    assert "apps.ocpp" in direct_selectors


def test_explain_role_app_selectors_preserves_one_shot_feature_pack_iterables():
    feature_packs = (feature_pack for feature_pack in ("screen-devices",))

    result = explain_role_app_selectors("Terminal", feature_packs=feature_packs)
    reasons = {item.selector: item.reasons for item in result.explanations}

    assert "apps.screens" in result.selectors
    assert "feature-pack:screen_devices" in reasons["apps.screens"]


def test_unknown_role_profile_disables_no_route_apps():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="UnknownDevice",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("unknown",),
    )

    assert disabled_route_apps == []


def test_unknown_role_or_feature_pack_raises_clear_errors():
    with pytest.raises(ValueError, match="Unknown role profile"):
        normalize_role_profile("unknown-device")

    with pytest.raises(ValueError, match="Unknown feature pack"):
        resolve_role_app_selectors("terminal", feature_packs=("unknown",))


@pytest.mark.parametrize(
    "feature_pack",
    ("charger-cutovers", "charger_cutovers", "unknown-pack"),
)
def test_unknown_role_resolution_raises_role_error_before_feature_pack(feature_pack):
    with pytest.raises(ValueError, match="Unknown role profile"):
        resolve_role_app_selectors("unknown-role", feature_packs=(feature_pack,))


def test_charger_cutovers_feature_pack_is_rejected_as_deprecated():
    with pytest.raises(ValueError, match="deprecated and unsupported"):
        resolve_role_app_selectors("control", feature_packs=("charger-cutovers",))


def test_camera_collection_feature_pack_is_removed():
    assert "camera_collection" not in FEATURE_PACK_APP_SELECTORS
    with pytest.raises(ValueError, match="Unknown feature pack"):
        resolve_role_app_selectors("terminal", feature_packs=("camera_collection",))


def test_setting_list_parser_accepts_common_separators():
    assert _split_setting_list("hosted-ocpp, screen_devices; printer_workflows") == (
        "hosted-ocpp",
        "screen_devices",
        "printer_workflows",
    )


def test_settings_resolver_keeps_full_fallback_without_lock_or_opt_in():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=None,
    )

    assert "apps.ocpp" in apps
    assert "apps.odoo" in apps
    assert "apps.shop" not in apps
    assert RETIRED_RUNTIME_APP_SELECTORS.isdisjoint(apps)


def test_settings_resolver_uses_profile_feature_packs_and_disables():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("hosted-ocpp",),
        disabled_apps=("repos",),
    )

    assert "apps.ocpp" in apps
    assert "apps.repos" not in apps


def test_watchtower_profile_includes_enabled_app_middleware():
    middleware = _middleware_for_profile("Watchtower")

    assert "apps.sites.middleware.SharePreviewPublicMiddleware" in middleware
    assert "apps.ops.middleware.ActiveOperationMiddleware" in middleware
    assert "apps.sigils.middleware.SigilContextMiddleware" in middleware
    assert "apps.sites.middleware.ViewHistoryMiddleware" in middleware


def test_control_profile_includes_all_app_middleware():
    middleware = _middleware_for_profile("Control")

    assert "apps.ops.middleware.ActiveOperationMiddleware" in middleware
    assert "apps.sites.middleware.SharePreviewPublicMiddleware" in middleware
    assert "apps.sigils.middleware.SigilContextMiddleware" in middleware
    assert "apps.sites.middleware.ViewHistoryMiddleware" in middleware


def test_middleware_resolver_skips_non_required_disabled_app_middleware():
    middleware = _middleware_for_profile(
        "Watchtower",
        disabled_apps=("ops",),
    )

    assert "django.contrib.sessions.middleware.SessionMiddleware" in middleware
    assert "apps.sites.middleware.SharePreviewPublicMiddleware" in middleware
    assert "apps.ops.middleware.ActiveOperationMiddleware" not in middleware
    assert "apps.sigils.middleware.SigilContextMiddleware" in middleware
    assert "apps.sites.middleware.ViewHistoryMiddleware" in middleware


def test_watchtower_profile_skips_control_hardware_beat_schedules():
    schedule = _beat_schedule_for_profile("Watchtower")

    assert "heartbeat" in schedule
    assert "github_monitor" in schedule
    assert "site_view_history_purge" in schedule
    assert "certificate_expiration_refresh" in schedule
    assert "thermometer_sampling" not in schedule
    assert "usb_lcd_status" not in schedule
    assert "llm_summary_lcd" not in schedule
    assert "ocpp_forwarding_push" not in schedule


def test_satellite_profile_skips_screen_beat_schedules():
    schedule = _beat_schedule_for_profile("Satellite")

    assert "thermometer_sampling" in schedule
    assert "usb_lcd_status" not in schedule
    assert "llm_summary_lcd" not in schedule


def test_control_profile_keeps_local_hardware_beat_schedules():
    schedule = _beat_schedule_for_profile("Control")

    assert "heartbeat" in schedule
    assert "thermometer_sampling" in schedule
    assert "usb_lcd_status" in schedule
    assert "llm_summary_lcd" in schedule
    assert "certificate_expiration_refresh" in schedule
    assert "ocpp_forwarding_push" in schedule


def test_beat_schedule_resolver_skips_explicitly_disabled_apps():
    schedule = _beat_schedule_for_profile(
        "Watchtower",
        disabled_apps=("certs",),
    )

    assert "certificate_expiration_refresh" not in schedule


def test_beat_schedule_resolver_keeps_full_fallback_schedule():
    schedule = _beat_schedule_for_profile("Watchtower", profile_enabled=False)

    assert {
        "auto_upgrade_check",
        "heartbeat",
        "thermometer_sampling",
        "ocpp_configuration_check",
        "ocpp_firmware_snapshot",
        "ocpp_offline_notifications",
        "ocpp_meter_value_purge",
        "ocpp_power_projection",
        "certificate_expiration_refresh",
        "site_view_history_purge",
        "log_retention_guard",
        "github_monitor",
    } <= set(schedule)
    assert "llm_summary_lcd" not in schedule
    assert "usb_lcd_status" not in schedule


def test_energy_billing_feature_pack_keeps_legacy_billing_apps_deprecated():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("energy_billing",),
    )

    assert "apps.energy" in apps
    assert "apps.rates" not in apps
    assert "apps.reports" in apps


def test_settings_resolver_rejects_disabling_local_sites():
    with pytest.raises(ValueError, match=r"apps\.sites cannot be disabled"):
        _resolve_installed_app_entries(
            node_role="Terminal",
            profile_enabled=True,
            enabled_app_lock_entries=None,
            disabled_apps=("sites",),
        )


def test_settings_resolver_uses_enabled_app_lock_with_dependency_closure():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries={
            "docs",
            "ops",
            "repos",
            "unknownlabel",
        },
        disabled_apps=("ops",),
    )

    assert "config.auth_app.AuthConfig" in apps
    assert "docs" not in apps
    assert "ops" not in apps
    assert "unknownlabel" not in apps
    assert "apps.ocpp" in apps
    assert "apps.docs" in apps
    assert "apps.ops" not in apps
    assert "apps.repos" in apps
    assert "apps.sites" in apps
    assert "apps.cards" in apps
    assert "apps.energy" in apps
    assert "apps.maps" in apps
    assert "apps.ftp" not in apps


@pytest.mark.parametrize("feature_pack", ("charger-cutovers", "charger_cutovers"))
def test_settings_resolver_rejects_deprecated_feature_pack_with_enabled_app_lock(
    feature_pack,
):
    with pytest.raises(ValueError, match="deprecated and unsupported"):
        _resolve_installed_app_entries(
            node_role="Watchtower",
            profile_enabled=False,
            enabled_app_lock_entries={"apps.ocpp", "apps.cards"},
            feature_packs=(feature_pack,),
        )


def test_settings_resolver_ignores_unknown_feature_pack_with_enabled_app_lock():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries={"apps.ocpp", "apps.cards"},
        feature_packs=("noep",),
    )

    assert "apps.ocpp" in apps
    assert "apps.cards" in apps


def test_settings_resolver_rejects_deprecated_feature_pack_before_unknown_fallback():
    with pytest.raises(ValueError, match="deprecated and unsupported"):
        _resolve_installed_app_entries(
            node_role="UnknownDevice",
            profile_enabled=True,
            enabled_app_lock_entries=None,
            feature_packs=("charger_cutovers",),
        )


def test_lock_route_providers_reject_deprecated_feature_pack_with_direct_lock():
    with pytest.raises(ValueError, match="deprecated and unsupported"):
        _resolve_route_provider_disabled_apps(
            node_role="Watchtower",
            profile_enabled=False,
            enabled_app_lock_entries=("apps.ocpp", "apps.cards"),
            enabled_app_lock_direct_entries=("apps.ocpp",),
            feature_packs=("charger_cutovers",),
        )


def test_lock_route_providers_ignore_unknown_feature_pack_with_direct_lock():
    disabled_route_apps = _resolve_route_provider_disabled_apps(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries=("apps.ocpp", "apps.cards"),
        enabled_app_lock_direct_entries=("apps.ocpp",),
        feature_packs=("noep",),
    )

    assert "apps.ocpp" not in disabled_route_apps


def test_enabled_app_lock_allows_disabling_repos_without_pruning_cards():
    apps = _resolve_installed_app_entries(
        node_role="Watchtower",
        profile_enabled=False,
        enabled_app_lock_entries={"cards", "repos"},
        disabled_apps=("repos",),
    )

    assert "apps.repos" not in apps
    assert "apps.cards" in apps


def test_settings_resolver_falls_back_for_unknown_bootstrap_role():
    apps = _resolve_installed_app_entries(
        node_role="UnknownDevice",
        profile_enabled=True,
        enabled_app_lock_entries=None,
        feature_packs=("file_transfer", "unknown_feature_pack"),
    )

    assert "apps.ocpp" in apps


def test_settings_resolver_rejects_unknown_feature_pack():
    with pytest.raises(ValueError, match="Unknown feature pack"):
        _resolve_installed_app_entries(
            node_role="Terminal",
            profile_enabled=True,
            enabled_app_lock_entries=None,
            feature_packs=("nope",),
        )


@pytest.mark.parametrize("disabled_app", ("cards", "energy", "sites"))
def test_settings_resolver_rejects_required_app_disables(disabled_app):
    with pytest.raises(ValueError, match=rf"apps\.{disabled_app} cannot be disabled"):
        _resolve_installed_app_entries(
            node_role="Terminal",
            profile_enabled=True,
            enabled_app_lock_entries=None,
            disabled_apps=(disabled_app,),
        )


def test_apps_registry_check_skips_disabled_local_app_declarations(settings):
    settings.PROJECT_LOCAL_APPS = ["apps.missing_disabled", "apps.docs"]
    settings.PROJECT_APPS = []
    settings.ARTHEXIS_EXTERNAL_APPS = []
    settings.INSTALLED_APPS = ["apps.docs"]

    errors = get_apps_registry_configuration_errors()

    assert not [
        error
        for error in errors
        if error.id == APPS_REGISTRY_ENTRY_NOT_IMPORTABLE_ID
        and error.obj == "apps.missing_disabled"
    ]


@pytest.mark.parametrize(
    "node_role", ("Terminal", "Watchtower", "Control", "Satellite")
)
def test_role_profile_settings_startup_succeeds(node_role):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["NODE_ROLE"] = node_role

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "check",
            "--fail-level",
            "ERROR",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_role_profile_rejects_disabling_required_ocpp(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "terminal-no-ocpp.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "terminal-no-ocpp-test.sqlite3")
    env["NODE_ROLE"] = "Terminal"
    env["ARTHEXIS_ROLE_APP_DISABLED_APPS"] = "apps.ocpp"

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "check",
            "--fail-level",
            "ERROR",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode != 0
    assert "apps.ocpp cannot be disabled" in result.stdout + result.stderr


def test_terminal_role_profile_loads_imager_management_command(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "terminal-imager.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "terminal-imager-test.sqlite3")
    env["NODE_ROLE"] = "Terminal"
    for key in (
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "help",
            "imager",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Build and safely write Raspberry Pi 4B image artifacts" in result.stdout


def test_satellite_role_profile_loads_migrations_management_command(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "satellite-migrations-command.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(
        tmp_path / "satellite-migrations-command-test.sqlite3"
    )
    env["NODE_ROLE"] = "Satellite"
    for key in (
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "help",
            "migrations",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Run migration maintenance workflows" in result.stdout


def test_satellite_role_profile_loads_good_management_command(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "satellite-good-command.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(
        tmp_path / "satellite-good-command-test.sqlite3"
    )
    env["NODE_ROLE"] = "Satellite"
    for key in (
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "help",
            "good",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Assess whether the current Arthexis setup looks good" in result.stdout


def test_control_role_profile_migration_graph_loads(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "control-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "control-profile-test.sqlite3")
    env["NODE_ROLE"] = "Control"
    for key in (
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
    ):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "showmigrations",
            "--plan",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_role_profile_migration_graph_loads_without_runtime_shop(
    tmp_path,
):
    env = os.environ.copy()
    for key in (
        "ARTHEXIS_DISABLED_APPS",
        "ARTHEXIS_FEATURE_PACKS",
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
    ):
        env.pop(key, None)
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "terminal-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "terminal-profile-test.sqlite3")
    env["NODE_ROLE"] = "Terminal"

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "showmigrations",
            "--plan",
            "--skip-checks",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "shop.0001_initial" not in result.stdout


def test_satellite_role_profile_migration_graph_loads_without_runtime_commerce_apps(
    tmp_path,
):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "satellite-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "satellite-profile-test.sqlite3")
    env["NODE_ROLE"] = "Satellite"
    env["ARTHEXIS_ROLE_APP_DISABLED_APPS"] = ",".join(
        (
            "apps.repos",
            "apps.rates",
            "apps.shop",
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "showmigrations",
            "--plan",
            "--skip-checks",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skills.0001_initial" in result.stdout
    assert "odoo.0001_initial" in result.stdout
    assert "pages.0001_initial" in result.stdout
    assert "repos.0001_initial" not in result.stdout


def test_satellite_role_profile_makemigrations_is_stable_without_runtime_commerce_apps(
    tmp_path,
):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "satellite-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "satellite-profile-test.sqlite3")
    env["NODE_ROLE"] = "Satellite"
    env["ARTHEXIS_ROLE_APP_DISABLED_APPS"] = ",".join(
        (
            "apps.repos",
            "apps.rates",
            "apps.shop",
        )
    )

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "makemigrations",
            "--check",
            "--dry-run",
            "--skip-checks",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "No changes detected" in result.stdout


def test_control_role_profile_env_refresh_startup_succeeds():
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["NODE_ROLE"] = "Control"

    result = subprocess.run(
        [
            sys.executable,
            "env-refresh.py",
            "--help",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_control_role_profile_admin_base_template_tolerates_missing_ops(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "control-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "control-profile-test.sqlite3")
    env["NODE_ROLE"] = "Control"
    env["ARTHEXIS_ROLE_APP_DISABLED_APPS"] = "ops"

    script = "\n".join(
        (
            "from types import SimpleNamespace",
            "from django.apps import apps",
            "from django.contrib.auth.models import AnonymousUser",
            "from django.template import Context",
            "from django.template.loader import get_template",
            "from django.test import RequestFactory, override_settings",
            "storages = {",
            "    'default': {",
            "        'BACKEND': 'django.core.files.storage.FileSystemStorage'",
            "    },",
            "    'staticfiles': {",
            "        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'",
            "    },",
            "}",
            "request = RequestFactory().get('/admin/login/')",
            "request.user = AnonymousUser()",
            "request.resolver_match = SimpleNamespace(url_name='login', kwargs={})",
            "context = {",
            "    'request': request,",
            "    'admin_badges': [],",
            "    'available_apps': [],",
            "    'is_popup': False,",
            "    'is_nav_sidebar_enabled': False,",
            "    'site_title': 'Django site admin',",
            "    'site_header': 'Django administration',",
            "    'has_permission': False,",
            "}",
            "assert not apps.is_installed('apps.ops')",
            "template = get_template('admin/base_site.html').template",
            "settings_override = override_settings(STORAGES=storages)",
            "settings_override.enable()",
            "try:",
            "    html = template.render(Context(context))",
            "finally:",
            "    settings_override.disable()",
            "print(len(html))",
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "shell",
            "-c",
            script,
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_index_template_tolerates_missing_actions_app(tmp_path):
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["ARTHEXIS_ROLE_APP_DISABLED_APPS"] = "actions"
    env["ARTHEXIS_SQLITE_PATH"] = str(tmp_path / "no-actions-profile.sqlite3")
    env["ARTHEXIS_SQLITE_TEST_PATH"] = str(tmp_path / "no-actions-profile-test.sqlite3")
    env["NODE_ROLE"] = "Watchtower"

    script = "\n".join(
        (
            "from types import SimpleNamespace",
            "from django.apps import apps",
            "from django.contrib.auth.models import AnonymousUser",
            "from django.template import Context",
            "from django.template.loader import get_template",
            "from django.test import RequestFactory, override_settings",
            "storages = {",
            "    'default': {",
            "        'BACKEND': 'django.core.files.storage.FileSystemStorage'",
            "    },",
            "    'staticfiles': {",
            "        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'",
            "    },",
            "}",
            "request = RequestFactory().get('/admin/')",
            "request.user = AnonymousUser()",
            "request.resolver_match = SimpleNamespace(url_name='index', kwargs={})",
            "context = {",
            "    'request': request,",
            "    'app_list': [],",
            "    'admin_badges': [],",
            "    'available_apps': [],",
            "    'is_popup': False,",
            "    'is_nav_sidebar_enabled': False,",
            "    'site_title': 'Django site admin',",
            "    'site_header': 'Django administration',",
            "    'has_permission': False,",
            "}",
            "assert not apps.is_installed('apps.actions')",
            "template = get_template('admin/index.html').template",
            "settings_override = override_settings(STORAGES=storages)",
            "settings_override.enable()",
            "try:",
            "    html = template.render(Context(context))",
            "finally:",
            "    settings_override.disable()",
            "print(len(html))",
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "shell",
            "-c",
            script,
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_role_profile_energy_billing_feature_pack_startup_succeeds():
    env = os.environ.copy()
    env["ARTHEXIS_ROLE_APP_PROFILES"] = "true"
    env["NODE_ROLE"] = "Watchtower"
    env["ARTHEXIS_ROLE_APP_FEATURE_PACKS"] = "energy_billing"

    result = subprocess.run(
        [
            sys.executable,
            "manage.py",
            "check",
            "--fail-level",
            "ERROR",
        ],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=STARTUP_CHECK_TIMEOUT,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
