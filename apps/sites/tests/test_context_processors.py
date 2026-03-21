import types

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.utils import OperationalError
from django.test import RequestFactory

from apps.features.models import Feature
from apps.groups.models import SecurityGroup
from apps.modules.models import Module
from apps.nodes.models import NodeFeature, NodeRole
from apps.sites import context_processors
from apps.sites.models import Landing


class _DummyQuerySet:
    def filter(self, **kwargs):
        return []

    def values_list(self, *args, **kwargs):
        return []


class _AnonymousUser:
    is_authenticated = False
    pk = None
    is_staff = False
    is_superuser = False
    groups = _DummyQuerySet()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("is_enabled", "enable_public_chat", "expected_chat_enabled"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_nav_links_chat_enabled_requires_feature_and_site_or_profile(
    monkeypatch, settings, is_enabled, enable_public_chat, expected_chat_enabled
):
    """Regression: chat enablement requires suite feature and a site/user opt-in signal."""

    cache.clear()
    request = RequestFactory().get("/")
    settings.PAGES_CHAT_ENABLED = True

    monkeypatch.setattr(
        context_processors.Node, "get_local", staticmethod(lambda: None)
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=enable_public_chat
        ),
    )

    Feature.objects.update_or_create(
        slug="staff-chat-bridge",
        defaults={"display": "Staff Chat Bridge", "is_enabled": is_enabled},
    )

    context = context_processors.nav_links(request)

    assert context["chat_enabled"] is expected_chat_enabled


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_respects_per_landing_module_pill_validation(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    module = Module.objects.create(path="/ocpp/", menu="Charge Points")
    landing_dashboard = Landing.objects.create(
        module=module,
        path="/ocpp/cpms/dashboard/",
        label="Dashboard",
        enabled=True,
    )
    landing_map = Landing.objects.create(
        module=module,
        path="/ocpp/charging-map/",
        label="Map",
        enabled=True,
    )

    request = RequestFactory().get("/")
    request.user = _AnonymousUser()

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )

    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        lambda _role: Module.objects.filter(pk=module.pk),
    )

    def dashboard_view(request):
        return None

    dashboard_view.module_pill_link_validator = lambda **kwargs: True
    dashboard_view.module_pill_link_validator_parameter_getter = lambda **kwargs: {
        "slug": "dashboard"
    }
    dashboard_view.module_pill_link_validator_cache_ttl = 30

    def map_view(request):
        return None

    map_view.module_pill_link_validator = lambda **kwargs: False
    map_view.module_pill_link_validator_parameter_getter = lambda **kwargs: {
        "slug": "map"
    }
    map_view.module_pill_link_validator_cache_ttl = 30

    def _resolve(path):
        if path == landing_dashboard.path:
            return types.SimpleNamespace(func=dashboard_view)
        if path == landing_map.path:
            return types.SimpleNamespace(func=map_view)
        raise AssertionError(path)

    monkeypatch.setattr(context_processors, "resolve", _resolve)

    context = context_processors.nav_links(request)

    nav_modules = context["nav_modules"]
    assert len(nav_modules) == 1
    labels = [landing.label for landing in nav_modules[0].enabled_landings]
    assert labels == ["Dashboard"]


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_caches_landing_validation_by_parameters(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    module = Module.objects.create(path="/ocpp/", menu="Charge Points")
    landing = Landing.objects.create(
        module=module,
        path="/ocpp/cpms/dashboard/",
        label="Dashboard",
        enabled=True,
    )

    request = RequestFactory().get("/")
    request.user = _AnonymousUser()

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )
    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        lambda _role: Module.objects.filter(pk=module.pk),
    )

    calls = {"count": 0}

    def dashboard_view(request):
        return None

    def validator(*, request, landing, version):
        calls["count"] += 1
        return version == "visible"

    dashboard_view.module_pill_link_validator = validator
    dashboard_view.module_pill_link_validator_cache_ttl = 30

    version = {"value": "hidden"}

    def parameter_getter(**kwargs):
        return {"version": version["value"]}

    dashboard_view.module_pill_link_validator_parameter_getter = parameter_getter

    monkeypatch.setattr(
        context_processors,
        "resolve",
        lambda path: types.SimpleNamespace(func=dashboard_view),
    )

    context_hidden_1 = context_processors.nav_links(request)
    context_hidden_2 = context_processors.nav_links(request)
    assert context_hidden_1["nav_modules"] == []
    assert context_hidden_2["nav_modules"] == []
    assert calls["count"] == 1

    version["value"] = "visible"
    context_visible = context_processors.nav_links(request)
    assert len(context_visible["nav_modules"]) == 1
    assert calls["count"] == 2


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_scopes_landing_validation_cache_per_user(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    module = Module.objects.create(path="/ocpp/", menu="Charge Points")
    Landing.objects.create(
        module=module,
        path="/ocpp/cpms/dashboard/",
        label="Dashboard",
        enabled=True,
    )

    users = [
        get_user_model().objects.create_user(username="viewer-1", password="pass"),
        get_user_model().objects.create_user(username="viewer-2", password="pass"),
    ]

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )
    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        lambda _role: Module.objects.filter(pk=module.pk),
    )

    calls = {"count": 0}

    def dashboard_view(request):
        return None

    def validator(*, request, landing):
        calls["count"] += 1
        return request.user.username == "viewer-1"

    dashboard_view.module_pill_link_validator = validator
    dashboard_view.module_pill_link_validator_cache_ttl = 30

    monkeypatch.setattr(
        context_processors,
        "resolve",
        lambda path: types.SimpleNamespace(func=dashboard_view),
    )

    for user, expected_count in zip(users, [1, 0], strict=True):
        request = RequestFactory().get("/")
        request.user = user
        context = context_processors.nav_links(request)
        assert len(context["nav_modules"]) == expected_count

    assert calls["count"] == 2


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_keeps_locked_landings_visible(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    module = Module.objects.create(path="/secure/", menu="Secure")
    Landing.objects.create(
        module=module,
        path="/secure/dashboard/",
        label="Dashboard",
        enabled=True,
    )

    request = RequestFactory().get("/secure/dashboard/")
    request.user = _AnonymousUser()

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )
    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        lambda _role: Module.objects.filter(pk=module.pk),
    )

    def secure_view(request):
        return None

    secure_view.login_required = True

    monkeypatch.setattr(
        context_processors,
        "resolve",
        lambda path: types.SimpleNamespace(func=secure_view),
    )

    context = context_processors.nav_links(request)

    assert len(context["nav_modules"]) == 1
    landing = context["nav_modules"][0].enabled_landings[0]
    assert landing.nav_is_locked is True
    assert landing.nav_lock_reason == "login"


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_excludes_group_gated_module_for_non_member(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    group = SecurityGroup.objects.create(name="Operators")
    module = Module.objects.create(
        path="/ops/",
        menu="Operators",
        security_group=group,
        security_mode=Module.SECURITY_EXCLUSIVE,
    )
    module.roles.add(role)
    Landing.objects.create(
        module=module,
        path="/ops/dashboard/",
        label="Dashboard",
        enabled=True,
    )
    user = get_user_model().objects.create_user(username="viewer", password="pass")

    request = RequestFactory().get("/ops/dashboard/")
    request.user = user

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )

    def ops_view(request):
        return None

    monkeypatch.setattr(
        context_processors,
        "resolve",
        lambda path: types.SimpleNamespace(func=ops_view),
    )

    context = context_processors.nav_links(request)

    assert context["nav_modules"] == []
    assert context["current_module"] is None


@pytest.mark.django_db
@pytest.mark.pr_origin(6225)
def test_nav_links_prefers_most_specific_current_module(monkeypatch):
    cache.clear()

    role = NodeRole.objects.create(name="Terminal")
    parent = Module.objects.create(path="/alpha/", menu="Alpha")
    child = Module.objects.create(path="/alpha/beta/", menu="Beta")
    Landing.objects.create(
        module=parent, path="/alpha/home/", label="Alpha Home", enabled=True
    )
    Landing.objects.create(
        module=child, path="/alpha/beta/home/", label="Beta Home", enabled=True
    )

    request = RequestFactory().get("/alpha/beta/page/")
    request.user = _AnonymousUser()

    monkeypatch.setattr(
        context_processors.Node,
        "get_local",
        staticmethod(lambda: types.SimpleNamespace(role=role)),
    )
    monkeypatch.setattr(
        context_processors,
        "get_site",
        lambda _request: types.SimpleNamespace(
            id=1, template=None, enable_public_chat=False
        ),
    )
    monkeypatch.setattr(
        context_processors, "user_in_site_operator_group", lambda user: False
    )
    monkeypatch.setattr(
        context_processors.Module.objects,
        "for_role",
        lambda _role: Module.objects.filter(pk__in=[parent.pk, child.pk]),
    )

    def alpha_view(request):
        return None

    monkeypatch.setattr(
        context_processors,
        "resolve",
        lambda path: types.SimpleNamespace(func=alpha_view),
    )

    context = context_processors.nav_links(request)

    assert context["current_module"].pk == child.pk
    assert request.current_module.pk == child.pk
