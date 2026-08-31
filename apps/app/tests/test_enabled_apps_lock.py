"""Tests for enabled application lock synchronization."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.app.models import (
    Application,
    _load_manifest_app_entries,
    _load_manifest_declared_app_entries,
    refresh_enabled_apps_lock,
)
from config.settings.apps import _resolve_installed_app_entries
from utils.enabled_apps_lock import (
    get_enabled_apps_lock_path,
    read_enabled_apps_lock,
    read_enabled_apps_lock_direct_entries,
    read_enabled_apps_lock_direct_sources,
    write_enabled_apps_lock,
)
from utils.role_app_profiles import (
    explain_role_app_selectors,
    get_direct_lock_app_selectors,
)


def test_load_manifest_app_entries_includes_runtime_apps_only():
    """Manifest discovery should include runtime apps and exclude legacy shims."""

    manifest_app_entries = _load_manifest_app_entries()
    expected_apps = {
        "apps.app",
        "apps.ocpp",
    }

    assert expected_apps.issubset(manifest_app_entries)
    assert all(
        not app_entry.startswith("apps._legacy.") for app_entry in manifest_app_entries
    )


def test_hosted_ocpp_mission_profile_keeps_core_charger_dependencies():
    """The mission profile must keep OCPP, RFID, and credit apps installed."""

    result = explain_role_app_selectors(
        "satellite",
        feature_packs=("hosted_ocpp",),
    )
    selectors = set(result.selectors)

    assert {
        "apps.ocpp",
        "apps.cards",
        "apps.energy",
        "apps.maps",
        "apps.nodes",
        "apps.protocols",
    }.issubset(selectors)


def test_odoo_keeps_discovery_dependency_when_explicitly_selected():
    result = explain_role_app_selectors(
        "terminal",
        explicit_apps=("apps.odoo",),
    )

    assert "apps.odoo" in result.selectors
    assert "apps.discovery" in result.selectors


def test_hosted_ocpp_mission_profile_excludes_terminal_direct_apps():
    result = explain_role_app_selectors(
        "satellite",
        feature_packs=("hosted_ocpp",),
    )
    direct_selectors = set(get_direct_lock_app_selectors(result))

    assert (
        not {
            "apps.imager",
            "apps.repos",
            "apps.skills",
            "apps.terminals",
        }
        & direct_selectors
    )


def test_read_enabled_apps_lock_strips_utf8_bom(tmp_path):
    lock_path = get_enabled_apps_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("\ufeffactions\n# ignored\ndocs\n", encoding="utf-8")

    assert read_enabled_apps_lock(tmp_path) == {"actions", "docs"}


def test_read_enabled_apps_lock_direct_entries_from_metadata(tmp_path):
    lock_path = get_enabled_apps_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        "\ufeff# direct: apps.docs\n# ignored\n# direct: apps.shop\napps.ocpp\n",
        encoding="utf-8",
    )

    assert read_enabled_apps_lock_direct_entries(tmp_path) == {
        "apps.docs",
        "apps.shop",
    }


def test_read_enabled_apps_lock_direct_sources_from_metadata(tmp_path):
    lock_path = get_enabled_apps_lock_path(tmp_path)
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        "# direct: apps.ocpp\n"
        "# direct-source: apps.ocpp charger-facing\n"
        "apps.ocpp\n",
        encoding="utf-8",
    )

    assert read_enabled_apps_lock_direct_sources(tmp_path) == {
        "apps.ocpp": "charger-facing"
    }


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_preserves_existing_direct_app_selectors(tmp_path):
    with override_settings(BASE_DIR=tmp_path):
        write_enabled_apps_lock(
            ("apps.shop", "apps.ocpp"),
            tmp_path,
            direct_apps=("apps.shop", "apps.ocpp"),
            direct_app_sources={"apps.ocpp": "charger-facing"},
        )
        Application.objects.bulk_create(
            [
                Application(name="shop", enabled=True),
                Application(name="ocpp", enabled=False),
            ]
        )

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries is not None
    assert "shop" in read_enabled_apps_lock(tmp_path)
    assert "apps.shop" in direct_entries
    assert "apps.ocpp" not in direct_entries
    assert read_enabled_apps_lock_direct_sources(tmp_path) == {}


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_preserves_explicit_optional_app_selectors(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs"},
    )
    monkeypatch.setattr(
        "apps.app.models._load_manifest_declared_app_entries",
        lambda: {"apps.docs", "apps.screens"},
    )
    with override_settings(BASE_DIR=tmp_path):
        write_enabled_apps_lock(
            ("apps.screens", "apps.docs"),
            tmp_path,
            direct_apps=("apps.screens",),
        )
        Application.objects.create(name="docs", enabled=True)

        refresh_enabled_apps_lock()

    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert "apps.screens" in lock_entries
    assert "apps.docs" in lock_entries
    assert read_enabled_apps_lock_direct_entries(tmp_path) == {"apps.screens"}


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_drops_explicitly_disabled_optional_app_selectors(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs"},
    )
    monkeypatch.setattr(
        "apps.app.models._load_manifest_declared_app_entries",
        lambda: {"apps.docs", "apps.screens"},
    )
    with override_settings(BASE_DIR=tmp_path):
        write_enabled_apps_lock(
            ("apps.screens", "apps.docs"),
            tmp_path,
            direct_apps=("apps.screens",),
        )
        Application.objects.create(name="screens", enabled=False)
        Application.objects.create(name="docs", enabled=True)

        refresh_enabled_apps_lock()

    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert "apps.screens" not in lock_entries
    assert "apps.docs" in lock_entries
    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    assert direct_entries is None or "apps.screens" not in direct_entries


@pytest.mark.django_db
def test_register_site_apps_creates_optional_apps_disabled_by_default(tmp_path):
    with override_settings(
        BASE_DIR=tmp_path,
        PROJECT_LOCAL_APPS=[],
        OPTIONAL_PROJECT_LOCAL_APPS=["apps.screens"],
    ):
        call_command("register_site_apps")

    optional_app = Application.objects.get(name="screens")

    assert optional_app.enabled is False


@pytest.mark.django_db
def test_register_site_apps_preserves_existing_optional_lock_opt_in(tmp_path):
    write_enabled_apps_lock(("apps.screens",), tmp_path)
    with override_settings(
        BASE_DIR=tmp_path,
        PROJECT_LOCAL_APPS=[],
        OPTIONAL_PROJECT_LOCAL_APPS=["apps.screens"],
    ):
        call_command("register_site_apps")

    optional_app = Application.objects.get(name="screens")

    assert optional_app.enabled is True


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_does_not_mark_seeded_enabled_apps_direct(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.ocpp"},
    )
    with override_settings(BASE_DIR=tmp_path):
        Application.objects.bulk_create([Application(name="ocpp", enabled=True)])

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert "apps.ocpp" in lock_entries
    assert "ocpp" in lock_entries
    assert direct_entries is None


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_seeds_missing_lock_public_route_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {
            "apps.ocpp",
            "apps.shop",
        },
    )
    with override_settings(BASE_DIR=tmp_path):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert {
        "apps.ocpp",
        "apps.shop",
    }.issubset(lock_entries)
    assert direct_entries is None


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_seeds_missing_lock_charger_facing_route_metadata(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {
            "apps.ocpp",
            "apps.shop",
        },
    )
    lock_dir = tmp_path / ".locks"
    lock_dir.mkdir()
    (lock_dir / "charger_facing.lck").write_text("", encoding="utf-8")
    with override_settings(BASE_DIR=tmp_path):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert "apps.ocpp" in lock_entries
    assert direct_entries == {"apps.ocpp"}
    assert read_enabled_apps_lock_direct_sources(tmp_path) == {
        "apps.ocpp": "charger-facing"
    }


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_seeds_metadata_less_charger_facing_lock(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs", "apps.ocpp"},
    )
    write_enabled_apps_lock(("apps.docs", "apps.ocpp"), tmp_path)
    (tmp_path / ".locks" / "charger_facing.lck").write_text("", encoding="utf-8")
    with override_settings(BASE_DIR=tmp_path):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)

    assert direct_entries == {"apps.ocpp"}
    assert read_enabled_apps_lock_direct_sources(tmp_path) == {
        "apps.ocpp": "charger-facing"
    }


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_uses_role_intent_for_missing_profile_lock(
    monkeypatch, tmp_path
):
    for name in (
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs", "apps.ocpp", "apps.shop"},
    )
    with override_settings(
        BASE_DIR=tmp_path,
        NODE_ROLE="Watchtower",
        ROLE_APP_PROFILES_ENABLED=True,
    ):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert {"apps.docs", "apps.ocpp", "apps.shop"}.issubset(lock_entries)
    assert direct_entries == {"apps.docs"}


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_source_tags_profile_seeded_direct_apps(
    monkeypatch, tmp_path
):
    for name in (
        "ARTHEXIS_ROLE_APP_FEATURE_PACKS",
        "ARTHEXIS_FEATURE_PACKS",
        "ARTHEXIS_ROLE_APP_DISABLED_APPS",
        "ARTHEXIS_DISABLED_APPS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs", "apps.ocpp", "apps.shop"},
    )
    with override_settings(
        BASE_DIR=tmp_path,
        NODE_ROLE="Watchtower",
        ROLE_APP_PROFILES_ENABLED=True,
    ):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    direct_sources = read_enabled_apps_lock_direct_sources(tmp_path)

    assert direct_entries == {"apps.docs"}
    assert direct_sources == {"apps.docs": "role-default:watchtower"}


@pytest.mark.django_db
def test_refresh_enabled_apps_lock_does_not_mark_manifest_apps_direct(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        "apps.app.models._load_manifest_app_entries",
        lambda: {"apps.docs", "apps.ocpp"},
    )
    with override_settings(BASE_DIR=tmp_path):
        Application.objects.all().delete()

        refresh_enabled_apps_lock()

    direct_entries = read_enabled_apps_lock_direct_entries(tmp_path)
    lock_entries = read_enabled_apps_lock(tmp_path)

    assert lock_entries is not None
    assert "apps.ocpp" in lock_entries
    assert direct_entries is None or "apps.ocpp" not in direct_entries


def test_enabled_app_lock_keeps_required_apps_when_lock_omits_them():
    resolved_entries = _resolve_installed_app_entries(
        node_role="terminal",
        profile_enabled=False,
        enabled_app_lock_entries=(),
    )

    assert "apps.app" in resolved_entries
    assert "apps.users" in resolved_entries


def test_app_registry_cannot_be_disabled_by_selector():
    with pytest.raises(ValueError, match="apps.app cannot be disabled"):
        _resolve_installed_app_entries(
            node_role="terminal",
            profile_enabled=False,
            enabled_app_lock_entries=("apps.app",),
            disabled_apps=("app",),
        )


def test_users_app_cannot_be_disabled_by_selector():
    with pytest.raises(ValueError, match="apps.users cannot be disabled"):
        _resolve_installed_app_entries(
            node_role="terminal",
            profile_enabled=False,
            enabled_app_lock_entries=("apps.users",),
            disabled_apps=("users",),
        )


def test_enabled_app_lock_rejects_pruned_required_apps():
    with pytest.raises(ValueError, match="apps.core cannot be omitted"):
        _resolve_installed_app_entries(
            node_role="terminal",
            profile_enabled=False,
            enabled_app_lock_entries=(),
            disabled_apps=("discovery",),
        )
