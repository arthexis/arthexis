"""Regression tests for env-refresh fixture planning and patch helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site

from apps.nginx.models.site_configuration import SiteConfiguration
from apps.release.models.package import Package
from apps.release.models.package_release import PackageRelease
from apps.sigils.models import SigilRoot


@pytest.fixture(scope="module")
def env_refresh_module():
    """Load the ``env-refresh.py`` script as a module for direct helper testing."""

    module_name = "tests.env_refresh_module"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing

    path = Path(__file__).resolve().parents[1] / "env-refresh.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.django_db
def test_plan_fixture_loading_uses_stored_hashes_when_mtimes_match(
    env_refresh_module, settings, tmp_path
):
    """Fixture planning should avoid rehashing unchanged fixture files."""

    settings.BASE_DIR = tmp_path
    fixture_path = tmp_path / "apps" / "core" / "fixtures" / "sample.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps([{"model": "sites.site", "fields": {"domain": "x"}}]))

    current_mtimes = env_refresh_module._fixture_mtime_cache(["apps/core/fixtures/sample.json"])
    stored_by_app = {"core": "stored-app-hash"}
    plan = env_refresh_module._plan_fixture_loading(
        fixtures=["apps/core/fixtures/sample.json"],
        force_db=False,
        clean=False,
        migrations_changed=False,
        migrations_ran=False,
        stored_hash="stored-fixture-hash",
        stored_by_app=stored_by_app,
        stored_mtimes=current_mtimes,
    )

    assert plan.current_hash == "stored-fixture-hash"
    assert plan.current_by_app == stored_by_app
    assert plan.mtimes_changed is False
    assert plan.should_load is False


@pytest.mark.django_db
def test_plan_fixture_loading_reloads_when_per_app_hash_changes(
    env_refresh_module, settings, tmp_path
):
    """Fixture planning should request reloads when app hashes diverge."""

    settings.BASE_DIR = tmp_path
    fixture_path = tmp_path / "apps" / "core" / "fixtures" / "sample.json"
    fixture_path.parent.mkdir(parents=True)
    fixture_path.write_text(json.dumps([{"model": "sites.site", "fields": {"domain": "x"}}]))

    current_mtimes = env_refresh_module._fixture_mtime_cache(["apps/core/fixtures/sample.json"])
    plan = env_refresh_module._plan_fixture_loading(
        fixtures=["apps/core/fixtures/sample.json"],
        force_db=False,
        clean=False,
        migrations_changed=False,
        migrations_ran=False,
        stored_hash="irrelevant",
        stored_by_app={"core": "stale"},
        stored_mtimes={"apps/core/fixtures/sample.json": current_mtimes["apps/core/fixtures/sample.json"] - 1},
    )

    assert plan.mtimes_changed is True
    assert plan.current_hash != "irrelevant"
    assert plan.current_by_app != {"core": "stale"}
    assert plan.should_load is True


@pytest.mark.django_db
def test_reconcile_existing_user_fixture_updates_fields_and_defers_unresolved_m2m(
    env_refresh_module, monkeypatch
):
    """Existing fixture users should update in place and preserve deferred M2M work."""

    user = get_user_model().objects.create_user(username="fixture-user", password="old")
    observed_save_kwargs: dict[str, object] = {}
    original_save = type(user).save

    def capturing_save(self, *args, **kwargs):
        observed_save_kwargs.update(kwargs)
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(type(user), "save", capturing_save)

    user_pk_map: dict[int, int] = {}
    pending_user_m2m = defaultdict(list)
    fixture = {
        "pk": 9,
        "fields": {
            "username": "fixture-user",
            "first_name": "Updated",
            "groups": [999999],
        },
    }

    reconciled = env_refresh_module._reconcile_existing_user_fixture(
        fixture,
        user_pk_map=user_pk_map,
        pending_user_m2m=pending_user_m2m,
    )

    user.refresh_from_db()
    assert reconciled is True
    assert user.first_name == "Updated"
    assert observed_save_kwargs == {"update_fields": ["first_name"]}
    assert user_pk_map == {9: user.pk}
    assert pending_user_m2m[user.pk] == [("groups", [999999])]


@pytest.mark.django_db
def test_upsert_sigilroot_fixture_updates_existing_prefix(env_refresh_module):
    """SigilRoot fixture upserts should resolve content types and update by prefix."""

    content_type = ContentType.objects.get_for_model(get_user_model())
    SigilRoot.objects.update_or_create(
        prefix="USER",
        defaults={"context_type": "entity", "content_type": content_type},
    )

    changed = env_refresh_module._upsert_sigilroot_fixture(
        SigilRoot,
        {
            "prefix": "USER",
            "context_type": "config",
            "content_type": ["sites", "site"],
        },
    )

    root = SigilRoot.objects.get(prefix="USER")
    assert changed is True
    assert root.context_type == "config"
    assert root.content_type == ContentType.objects.get_by_natural_key("sites", "site")


@pytest.mark.django_db
def test_site_and_configuration_upserts_update_existing_records(env_refresh_module):
    """Site-related fixture upserts should update rows keyed by their stable identifiers."""

    Site.objects.update_or_create(pk=1, defaults={"domain": "old.example", "name": "Old"})
    SiteConfiguration.objects.create(name="public.example", mode="internal", role="Terminal")
    site_defaults: dict[str, dict[str, object]] = {}

    site_changed = env_refresh_module._upsert_site_fixture(
        {"domain": "old.example", "name": "New"},
        site_defaults=site_defaults,
    )
    config_changed = env_refresh_module._upsert_site_configuration(
        {
            "name": "public.example",
            "mode": "public",
            "role": "Gateway",
            "last_message": "ignored",
        }
    )

    site = Site.objects.get(domain="old.example")
    config = SiteConfiguration.objects.get(name="public.example")
    assert site_changed is True
    assert config_changed is True
    assert site.name == "New"
    assert site_defaults == {"old.example": {"domain": "old.example", "name": "New"}}
    assert config.mode == "public"
    assert config.role == "Gateway"
    assert config.last_message == ""


@pytest.mark.django_db
def test_dedupe_package_release_fixture_skips_existing_version(env_refresh_module):
    """Package release fixture dedupe should skip versions already present."""

    package = Package.objects.create(name="fixture-package")
    PackageRelease.objects.create(package=package, version="1.2.3")

    skipped = env_refresh_module._dedupe_package_release_fixture(
        {"fields": {"version": "1.2.3"}}
    )
    allowed = env_refresh_module._dedupe_package_release_fixture(
        {"fields": {"version": "2.0.0"}}
    )

    assert skipped is True
    assert allowed is False
