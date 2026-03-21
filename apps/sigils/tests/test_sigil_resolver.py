import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
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



def test_parse_token_parts_parses_filter_key_and_param():
    parts = sigil_resolver._parse_token_parts("USR:username=[ENV.current-user].email=display")

    assert parts.root_name == "USR"
    assert parts.filter_field == "username"
    assert parts.instance_id == "[ENV.current-user]"
    assert parts.key == "email"
    assert parts.param == "display"



def test_parse_token_parts_rejects_incomplete_filter():
    with pytest.raises(sigil_resolver.TokenParseError):
        sigil_resolver._parse_token_parts("USR:username")


@pytest.mark.django_db
def test_resolve_sigils_entity_aggregate_total_for_field(user_root):
    user_model = get_user_model()
    baseline = sigil_resolver.resolve_sigils("[USR=id:total]")
    baseline_total = int(baseline) if baseline else 0
    first_user = user_model.objects.create(username="alpha")
    second_user = user_model.objects.create(username="bravo")

    result = sigil_resolver.resolve_sigils("[USR=id:total]")

    assert result == str(baseline_total + first_user.id + second_user.id)


@pytest.mark.django_db
def test_resolve_sigils_explicit_entity_miss_preserves_placeholder(user_root):
    result = sigil_resolver.resolve_sigils("[USR=missing.email]")

    assert result == "[USR=missing.email]"


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
def test_resolve_sigils_conf_looks_up_settings_value(settings):
    SigilRoot.objects.update_or_create(
        prefix="CONF", defaults={"context_type": SigilRoot.Context.CONFIG}
    )
    settings.SIGIL_TEST_VALUE = "configured"

    result = sigil_resolver.resolve_sigils("[CONF.sigil-test-value]")

    assert result == "configured"


@pytest.mark.django_db
def test_resolve_sigils_manager_database_errors_propagate(monkeypatch, user_root):
    user_model = get_user_model()

    def explode(*args, **kwargs):
        raise DatabaseError("boom")

    monkeypatch.setattr(user_model.objects, "explode", explode, raising=False)

    with pytest.raises(DatabaseError, match="boom"):
        sigil_resolver.resolve_sigils("[USR=explode]")


@pytest.mark.django_db
def test_resolve_sigils_entity_failures_preserve_placeholder(user_root):
    result = sigil_resolver.resolve_sigils("[USR:missing-field=value.email]")

    assert result == "[USR:missing-field=value.email]"
