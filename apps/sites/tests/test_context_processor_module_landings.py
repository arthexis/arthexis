from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sites.models import Site
from django.db.utils import OperationalError
from django.test import RequestFactory

from apps.groups.models import SecurityGroup
from apps.modules.models import Module
from apps.nodes.models import NodeFeature, NodeRole
from apps.sites import context_processors
from apps.sites.context_processors import (
    _limit_anon_ocpp_landings,
    _module_allows_site_visibility_show,
    _sort_module_landings,
)
from apps.sites.models import Landing, SiteModuleVisibility

pytestmark = pytest.mark.django_db


def test_sort_module_landings_prioritizes_ocpp_navigation_paths():
    landings = [
        SimpleNamespace(path="/ocpp/charge-point-models/"),
        SimpleNamespace(path="/ocpp/cpms/dashboard/"),
        SimpleNamespace(path="/ocpp/reports/energy/"),
    ]

    ordered_paths = [
        landing.path for landing in _sort_module_landings("/ocpp/", landings)
    ]

    assert ordered_paths == [
        "/ocpp/cpms/dashboard/",
        "/ocpp/reports/energy/",
        "/ocpp/charge-point-models/",
    ]


def test_sort_module_landings_keeps_non_ocpp_order():
    landings = [
        SimpleNamespace(path="/docs/library/"),
        SimpleNamespace(path="/docs/help/"),
    ]

    ordered_paths = [
        landing.path for landing in _sort_module_landings("/docs/", landings)
    ]

    assert ordered_paths == [
        "/docs/library/",
        "/docs/help/",
    ]


def test_limit_anon_ocpp_landings_keeps_charge_point_entries_for_guests():
    module = SimpleNamespace(path="/ocpp/", menu="Charge Points")
    landings = [
        SimpleNamespace(path="/ocpp/cpms/dashboard/"),
        SimpleNamespace(path="/ocpp/charge-point-models/"),
    ]

    filtered = _limit_anon_ocpp_landings(module, SimpleNamespace(), landings)

    assert [landing.path for landing in filtered] == [
        "/ocpp/cpms/dashboard/",
        "/ocpp/charge-point-models/",
    ]
    assert module.menu == "Charge Points"


def test_soft_deleted_landing_is_excluded_from_navigation(rf: RequestFactory) -> None:
    """Retired landing rows must not keep their module pill visible."""

    module = Module.objects.create(path="/retired/", menu="Retired")
    landing = Landing.objects.create(
        module=module,
        path="/retired/",
        label="Retired",
        enabled=True,
        is_seed_data=True,
    )
    landing.delete()
    request = rf.get("/")
    request.user = AnonymousUser()

    annotated = context_processors._annotate_module_landings(
        module,
        request,
        feature_checker=SimpleNamespace(is_enabled=lambda slug: True),
        role_id=None,
        site_id=None,
        user_cache_key=None,
        user_group_names=set(),
    )

    assert annotated is None


def _module_paths(context):
    return [module.path for module in context["nav_modules"]]


def _prepare_site_visibility_modules():
    watchtower_role, _ = NodeRole.objects.get_or_create(name="Watchtower")
    control_role, _ = NodeRole.objects.get_or_create(name="Control")

    shop_module, _ = Module.objects.get_or_create(
        path="/shop/",
        defaults={"menu": "Card Shop", "priority": 10},
    )
    shop_module.menu = "Card Shop"
    shop_module.priority = 10
    shop_module.is_deleted = False
    shop_module.security_group = None
    shop_module.security_mode = Module.SECURITY_INCLUSIVE
    shop_module.save(
        update_fields=[
            "menu",
            "priority",
            "is_deleted",
            "security_group",
            "security_mode",
        ]
    )
    shop_module.roles.set([watchtower_role])
    shop_module.features.clear()
    shop_landing, _ = Landing.objects.update_or_create(
        module=shop_module,
        path="/",
        defaults={"label": "Card Shop", "enabled": True},
    )
    shop_landing.is_deleted = False
    shop_landing.enabled = True
    shop_landing.save(update_fields=["is_deleted", "enabled"])

    docs_module, _ = Module.objects.get_or_create(
        path="/docs/",
        defaults={"menu": "Docs", "priority": 5},
    )
    docs_module.menu = "Docs"
    docs_module.priority = 5
    docs_module.is_deleted = False
    docs_module.security_group = None
    docs_module.security_mode = Module.SECURITY_INCLUSIVE
    docs_module.save(
        update_fields=[
            "menu",
            "priority",
            "is_deleted",
            "security_group",
            "security_mode",
        ]
    )
    docs_module.roles.set([control_role])
    docs_module.features.clear()
    docs_landing, _ = Landing.objects.update_or_create(
        module=docs_module,
        path="/",
        defaults={"label": "Docs", "enabled": True},
    )
    docs_landing.is_deleted = False
    docs_landing.enabled = True
    docs_landing.save(update_fields=["is_deleted", "enabled"])

    return watchtower_role, shop_module, docs_module


def _patch_nav_chrome(monkeypatch):
    monkeypatch.setattr(
        context_processors, "_build_chat_context", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(context_processors, "_load_header_references", lambda *args: [])
    monkeypatch.setattr(context_processors, "_load_latest_site_highlight", lambda: None)
    monkeypatch.setattr(
        context_processors, "_parse_user_story_attachment_limit", lambda: 3
    )
    monkeypatch.setattr(context_processors, "_select_favicon_url", lambda *args: "")
    monkeypatch.setattr(context_processors, "_select_site_template", lambda *args: None)


def test_build_chat_context_ignores_unavailable_chat_profile_storage():
    class UserWithBrokenChatProfile:
        is_authenticated = True
        pk = 1

        def get_profile(self, profile_cls):
            raise OperationalError("no such table: chats_chatavatar")

    context = context_processors._build_chat_context(UserWithBrokenChatProfile())

    assert context == {"chat_opt_in_checked": False}


def test_nav_links_applies_site_hide_and_anonymous_show_rules(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, shop_module, docs_module = _prepare_site_visibility_modules()
    SiteModuleVisibility.objects.create(
        site=site,
        module=shop_module,
        visibility=SiteModuleVisibility.VISIBILITY_HIDE,
        audience=SiteModuleVisibility.AUDIENCE_ALL,
    )
    SiteModuleVisibility.objects.create(
        site=site,
        module=docs_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ANONYMOUS,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = AnonymousUser()

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    paths = _module_paths(context)
    assert "/docs/" in paths
    assert "/shop/" not in paths


def test_nav_links_applies_arthexis_authenticated_module_rules(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
    django_user_model,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, shop_module, docs_module = _prepare_site_visibility_modules()
    SiteModuleVisibility.objects.create(
        site=site,
        module=shop_module,
        visibility=SiteModuleVisibility.VISIBILITY_HIDE,
        audience=SiteModuleVisibility.AUDIENCE_ANONYMOUS,
    )
    SiteModuleVisibility.objects.create(
        site=site,
        module=docs_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ALL,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = django_user_model.objects.create_user(
        username="arthexis-site-user",
        email="site-user@example.com",
        password="password",
    )

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    paths = _module_paths(context)
    assert "/docs/" in paths
    assert "/shop/" in paths


def test_nav_links_show_rule_respects_exclusive_security_group(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
    django_user_model,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, _shop_module, _docs_module = _prepare_site_visibility_modules()
    restricted_group = SecurityGroup.objects.create(name="restricted-module-group")
    restricted_module = Module.objects.create(
        path="/restricted/",
        menu="Restricted",
        priority=20,
        security_group=restricted_group,
        security_mode=Module.SECURITY_EXCLUSIVE,
    )
    restricted_module.roles.set([watchtower_role])
    Landing.objects.create(
        module=restricted_module,
        path="/restricted/",
        label="Restricted",
        enabled=True,
    )
    SiteModuleVisibility.objects.create(
        site=site,
        module=restricted_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ALL,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = django_user_model.objects.create_user(
        username="restricted-outsider",
        email="restricted-outsider@example.com",
        password="password",
    )

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    assert "/restricted/" not in _module_paths(context)

    request.user.groups.add(restricted_group)

    context = context_processors.nav_links(request)

    assert "/restricted/" in _module_paths(context)


def test_module_allows_site_visibility_show_fails_closed_without_user() -> None:
    restricted_module = SimpleNamespace(
        security_group_id=1,
        security_mode=Module.SECURITY_EXCLUSIVE,
    )

    allowed = _module_allows_site_visibility_show(
        restricted_module,
        None,
        {1},
    )

    assert not allowed


def test_site_module_visibility_can_replace_soft_deleted_seed_rule() -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    _watchtower_role, shop_module, _gallery_module = _prepare_site_visibility_modules()
    rule = SiteModuleVisibility.objects.create(
        site=site,
        module=shop_module,
        visibility=SiteModuleVisibility.VISIBILITY_HIDE,
        audience=SiteModuleVisibility.AUDIENCE_ALL,
        is_seed_data=True,
    )

    rule.delete()
    replacement = SiteModuleVisibility.objects.create(
        site=site,
        module=shop_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ALL,
    )

    assert replacement.pk != rule.pk
    assert SiteModuleVisibility.objects.filter(pk=rule.pk).count() == 0
    assert SiteModuleVisibility.all_objects.filter(pk=rule.pk, is_deleted=True).exists()


def test_nav_links_keeps_unconfigured_watchtower_shop_visibility(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="watchtower.test", defaults={"name": "Watchtower"}
    )
    watchtower_role, _, _ = _prepare_site_visibility_modules()
    request = rf.get("/", HTTP_HOST="watchtower.test")
    request.user = AnonymousUser()

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    paths = _module_paths(context)
    assert "/shop/" in paths
    assert "/docs/" not in paths


def test_nav_links_ignores_authenticated_show_rule_for_anonymous_users(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, _, docs_module = _prepare_site_visibility_modules()
    SiteModuleVisibility.objects.create(
        site=site,
        module=docs_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_AUTHENTICATED,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = AnonymousUser()

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    assert "/docs/" not in _module_paths(context)


def test_nav_links_respects_feature_gates_for_anonymous_show_rules(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, _, docs_module = _prepare_site_visibility_modules()
    feature, _ = NodeFeature.objects.get_or_create(
        slug="docs-test-gate",
        defaults={"display": "Docs test gate"},
    )
    docs_module.features.set([feature])
    SiteModuleVisibility.objects.create(
        site=site,
        module=docs_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ANONYMOUS,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = AnonymousUser()

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )
    monkeypatch.setattr(
        context_processors,
        "FeatureChecker",
        lambda: SimpleNamespace(is_enabled=lambda slug: False),
    )

    context = context_processors.nav_links(request)

    assert "/docs/" not in _module_paths(context)


def test_nav_links_show_rule_keeps_already_visible_locked_module(
    monkeypatch: pytest.MonkeyPatch,
    rf: RequestFactory,
) -> None:
    site, _ = Site.objects.get_or_create(
        domain="arthexis.com", defaults={"name": "Arthexis"}
    )
    watchtower_role, shop_module, _docs_module = _prepare_site_visibility_modules()
    shop_module.landings.all().delete()
    Landing.objects.create(
        module=shop_module,
        path="/shop/upload/",
        label="Locked Upload",
        enabled=True,
    )
    SiteModuleVisibility.objects.create(
        site=site,
        module=shop_module,
        visibility=SiteModuleVisibility.VISIBILITY_SHOW,
        audience=SiteModuleVisibility.AUDIENCE_ANONYMOUS,
    )
    request = rf.get("/", HTTP_HOST="arthexis.com")
    request.user = AnonymousUser()

    _patch_nav_chrome(monkeypatch)
    monkeypatch.setattr(
        context_processors,
        "_initialize_request_badges",
        lambda request: (site, None, watchtower_role),
    )

    context = context_processors.nav_links(request)

    assert "/shop/" in _module_paths(context)
