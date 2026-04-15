import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot
from apps.sigils.sigil_context import clear_request, set_request


@pytest.fixture
def user_root():
    user_model = get_user_model()
    root, _ = SigilRoot.objects.update_or_create(
        prefix="USR",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(user_model),
        },
    )
    return root


@pytest.mark.django_db
def test_resolve_sigils_filters_and_fetches_field(user_root):
    user_model = get_user_model()
    user = user_model.objects.create(username="SigilUser", email="sigil@example.com")

    result = sigil_resolver.resolve_sigils("[USR:username=sigiluser.email]")

    assert result == user.email



@pytest.mark.django_db
def test_resolve_sigils_request_values():
    SigilRoot.objects.update_or_create(
        prefix="REQ", defaults={"context_type": SigilRoot.Context.REQUEST}
    )
    factory = RequestFactory()
    request = factory.get(
        "/example/path?foo=bar",
        HTTP_X_CUSTOM_HEADER="hello",
    )
    set_request(request)
    try:
        assert sigil_resolver.resolve_sigils("[REQ.method]") == "GET"
        assert sigil_resolver.resolve_sigils("[REQ.path]") == "/example/path"
        assert sigil_resolver.resolve_sigils("[REQ.query=foo]") == "bar"
        assert (
            sigil_resolver.resolve_sigils("[REQ.header=X-Custom-Header]") == "hello"
        )
    finally:
        clear_request()


@pytest.mark.django_db
def test_resolve_sigils_uses_default_entity_instance(monkeypatch):
    SigilRoot.objects.update_or_create(
        prefix="NODE",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
        },
    )

    role = NodeRole.objects.create(name="Gateway")
    node = Node.objects.create(
        hostname="gway-001",
        address="127.0.0.1",
        mac_address="00:11:22:33:44:55",
        port=8888,
        public_endpoint="gway-001",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    result = sigil_resolver.resolve_sigils("[NODE.ROLE]")

    assert result == role.name


@pytest.mark.django_db
def test_resolve_sigils_uses_default_entity_instance_with_unrelated_current(monkeypatch):
    SigilRoot.objects.update_or_create(
        prefix="NODE",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
        },
    )

    role = NodeRole.objects.create(name="Router")
    node = Node.objects.create(
        hostname="router-001",
        address="127.0.0.2",
        mac_address="00:11:22:33:44:66",
        port=9999,
        public_endpoint="router-001",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))
    current_user = get_user_model().objects.create(username="context-user")

    result = sigil_resolver.resolve_sigils("[NODE.ROLE]", current=current_user)

    assert result == role.name



@pytest.mark.django_db
def test_resolve_sigils_empty_nested_selector_does_not_fall_back_to_default_instance(monkeypatch, user_root):
    SigilRoot.objects.update_or_create(
        prefix="NODE",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
        },
    )
    role = NodeRole.objects.create(name="Default Role")
    node = Node.objects.create(
        hostname="default-node",
        address="127.0.0.4",
        mac_address="00:11:22:33:44:88",
        port=7788,
        public_endpoint="default-node",
        role=role,
    )
    monkeypatch.setattr(Node, "get_local", classmethod(lambda cls: node))

    result = sigil_resolver.resolve_sigils("[NODE=\"[USR=missing.email]\".role]")

    assert result == ""


@pytest.mark.django_db
def test_resolve_sigils_entity_manager_dispatch_ignores_unrelated_current(user_root):
    user_model = get_user_model()
    user_model.objects.create(username="alpha")
    user_model.objects.create(username="bravo")
    role = NodeRole.objects.create(name="Manager Dispatch")
    current_node = Node.objects.create(
        hostname="dispatch-001",
        address="127.0.0.3",
        mac_address="00:11:22:33:44:77",
        port=7777,
        public_endpoint="dispatch-001",
        role=role,
    )

    result = sigil_resolver.resolve_sigils("[USR=all]", current=current_node)

    assert "alpha" in result
    assert "bravo" in result


@pytest.mark.django_db
def test_resolve_sigils_manager_database_errors_propagate(monkeypatch, user_root):
    user_model = get_user_model()

    def explode(*args, **kwargs):
        raise DatabaseError("boom")

    monkeypatch.setattr(user_model.objects, "explode", explode, raising=False)

    with pytest.raises(DatabaseError, match="boom"):
        sigil_resolver.resolve_sigils("[USR=explode]")


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("token", "assertion"),
    [
        ("[USR=all]", lambda payload: len(payload) >= 2),
        ("[USR=manager_count]", lambda payload: payload == "2"),
    ],
)
def test_resolve_sigils_table_driven_manager_method_dispatch(user_root, token, assertion, monkeypatch):
    user_model = get_user_model()
    user_model.objects.create(username="manager-alpha")
    user_model.objects.create(username="manager-bravo")
    monkeypatch.setattr(user_model.objects, "manager_count", lambda: 2, raising=False)

    resolved = sigil_resolver.resolve_sigils(token)
    parsed = json.loads(resolved) if resolved.startswith("[") else resolved

    assert assertion(parsed)


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("[USR=:count]", "2"),
        ("[USR=id:total]", lambda baseline, users: str(baseline + sum(users))),
    ],
)
def test_resolve_sigils_table_driven_aggregate_requests(user_root, token, expected):
    user_model = get_user_model()
    baseline_total = int(sigil_resolver.resolve_sigils("[USR=id:total]") or "0")
    first_user = user_model.objects.create(username="agg-alpha")
    second_user = user_model.objects.create(username="agg-bravo")
    user_ids = (first_user.id, second_user.id)

    resolved = sigil_resolver.resolve_sigils(token)
    expected_value = expected if isinstance(expected, str) else expected(baseline_total, user_ids)
    assert resolved == expected_value


@pytest.mark.django_db
def test_resolve_sigils_allowed_roots_limits_resolution(monkeypatch):
    monkeypatch.setenv("SIGIL_POLICY_TEST", "resolved")
    SigilRoot.objects.update_or_create(
        prefix="ENV",
        defaults={"context_type": SigilRoot.Context.CONFIG},
    )

    assert (
        sigil_resolver.resolve_sigils(
            "[ENV.SIGIL_POLICY_TEST]",
            allowed_roots={"REQ"},
        )
        == "[ENV.SIGIL_POLICY_TEST]"
    )
    assert (
        sigil_resolver.resolve_sigils(
            "[ENV.SIGIL_POLICY_TEST]",
            allowed_roots={"ENV"},
        )
        == "resolved"
    )


@pytest.mark.django_db
def test_get_user_safe_sigil_roots_normalizes_prefixes():
    SigilRoot.objects.update_or_create(
        prefix="safe-root",
        defaults={
            "context_type": SigilRoot.Context.REQUEST,
            "is_user_safe": True,
        },
    )
    SigilRoot.objects.update_or_create(
        prefix="unsafe_root",
        defaults={
            "context_type": SigilRoot.Context.REQUEST,
            "is_user_safe": False,
        },
    )

    assert sigil_resolver.get_user_safe_sigil_roots() == {"SAFE_ROOT"}
