import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole

from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot
from apps.sigils.sigil_context import clear_request, set_request


@pytest.mark.django_db
def test_resolve_sigils_unknown_root_returns_placeholder():
    result = sigil_resolver.resolve_sigils("Value: [UNKNOWN.key]")

    assert result.endswith("[UNKNOWN.key]")


@pytest.mark.django_db
def test_resolve_sigils_env_normalizes_key(monkeypatch):
    SigilRoot.objects.update_or_create(
        prefix="ENV", defaults={"context_type": SigilRoot.Context.CONFIG}
    )
    monkeypatch.setenv("EXAMPLE_VAR", "42")

    result = sigil_resolver.resolve_sigils("[env.example-var]")

    assert result == "42"


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
def test_resolve_sigils_aggregates_count(user_root):
    user_model = get_user_model()
    user_model.objects.create(username="user1")
    user_model.objects.create(username="user2")

    result = sigil_resolver.resolve_sigils("[USR=:count]")

    assert result == "2"


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
