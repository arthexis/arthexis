import json

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import DatabaseError
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
from apps.sigils import sigil_resolver
from apps.sigils.models import SigilRoot
from apps.sigils.sigil_builder import generate_model_sigils
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


@pytest.fixture
def node_root():
    root, _ = SigilRoot.objects.update_or_create(
        prefix="CP",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
            "is_user_safe": True,
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
def test_pipeline_v2_parses_uppercase_pipeline(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Charging")
    Node.objects.create(
        hostname="SIM-CP-1",
        address="127.0.0.11",
        mac_address="00:11:22:33:44:12",
        port=9001,
        public_endpoint="SIM-CP-1",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils("[CP:hostname:SIM-CP-1|GET:role]")

    assert resolved == "Charging"


@pytest.mark.django_db
def test_pipeline_v2_normalizes_mixed_case_root_and_action(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Charging")
    Node.objects.create(
        hostname="SIM-CP-1",
        address="127.0.0.12",
        mac_address="00:11:22:33:44:13",
        port=9002,
        public_endpoint="SIM-CP-1",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils("[cp:hostname:SIM-CP-1|field:role]")

    assert resolved == "Charging"


@pytest.mark.django_db
def test_pipeline_v2_coexists_with_dot_and_parenthesis_sigils(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Router")
    Node.objects.create(
        hostname="SIM-CP-2",
        address="127.0.0.13",
        mac_address="00:11:22:33:44:14",
        port=9003,
        public_endpoint="SIM-CP-2",
        role=role,
    )
    user_model = get_user_model()
    user_model.objects.create(username="sigiladmin")

    result = sigil_resolver.resolve_sigils(
        "[USR:username=sigiladmin.username]-[CP:hostname:SIM-CP-2|GET:role]"
    )

    assert result == "sigiladmin-Router"


@pytest.mark.django_db
def test_pipeline_v2_aggregate_action_payload_resolves(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Counter")
    Node.objects.create(
        hostname="SIM-CP-AGG-1",
        address="127.0.1.1",
        mac_address="00:11:22:33:44:31",
        port=9011,
        public_endpoint="SIM-CP-AGG-1",
        role=role,
    )
    Node.objects.create(
        hostname="SIM-CP-AGG-2",
        address="127.0.1.2",
        mac_address="00:11:22:33:44:32",
        port=9012,
        public_endpoint="SIM-CP-AGG-2",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils("[CP:|COUNT:port]")

    assert resolved == "2"


@pytest.mark.django_db
def test_pipeline_v2_root_and_action_can_omit_colons(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="No-Colon")
    Node.objects.create(
        hostname="SIM-CP-NC-1",
        address="127.0.1.3",
        mac_address="00:11:22:33:44:33",
        port=9013,
        public_endpoint="SIM-CP-NC-1",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils("[CP|COUNT:port]")

    assert resolved == "1"


@pytest.mark.django_db
def test_pipeline_v2_filter_uses_safe_bounded_serialization(settings, user_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    settings.SIGILS_PIPELINE_FILTER_LIMIT = 1
    user_model = get_user_model()
    user_model.objects.create_user(
        username="filter-a",
        email="filter@example.com",
        password="abc12345",
    )
    user_model.objects.create_user(
        username="filter-b",
        email="filter@example.com",
        password="abc12345",
    )

    resolved = sigil_resolver.resolve_sigils("[USR:|FILTER:email:filter@example.com]")
    payload = json.loads(resolved)

    assert len(payload) == 1
    assert payload[0]["email"] == "filter@example.com"
    assert "password" not in payload[0]


@pytest.mark.django_db
def test_pipeline_v2_falls_back_to_legacy_parser_for_pipe_parameters(settings):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    SigilRoot.objects.update_or_create(
        prefix="REQ", defaults={"context_type": SigilRoot.Context.REQUEST}
    )
    factory = RequestFactory()
    request = factory.get("/example/path?foo%7Cbar=baz")
    set_request(request)
    try:
        resolved = sigil_resolver.resolve_sigils("[REQ.query=foo|bar]")
    finally:
        clear_request()

    assert resolved == "baz"


@pytest.mark.django_db
def test_pipeline_v2_user_safe_gating_degrades_disallowed_action(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Charging")
    Node.objects.create(
        hostname="SIM-CP-3",
        address="127.0.0.14",
        mac_address="00:11:22:33:44:15",
        port=9004,
        public_endpoint="SIM-CP-3",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils(
        "[CP:hostname:SIM-CP-3|GET:role]",
        allowed_roots={"CP"},
        allowed_actions={"COUNT"},
    )

    assert resolved == "[CP:hostname:SIM-CP-3|GET:role]"


@pytest.mark.django_db
def test_pipeline_v2_feature_flag_can_disable_pipeline_parsing(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = False
    role = NodeRole.objects.create(name="Disabled")
    Node.objects.create(
        hostname="SIM-CP-4",
        address="127.0.0.15",
        mac_address="00:11:22:33:44:16",
        port=9005,
        public_endpoint="SIM-CP-4",
        role=role,
    )

    resolved = sigil_resolver.resolve_sigils("[CP:hostname:SIM-CP-4|GET:role]")

    assert resolved == "[CP:hostname:SIM-CP-4|GET:role]"


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
        assert sigil_resolver.resolve_sigils("[REQ.header=X-Custom-Header]") == "hello"
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
def test_resolve_sigils_uses_default_entity_instance_with_unrelated_current(
    monkeypatch,
):
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
def test_resolve_sigils_empty_nested_selector_does_not_fall_back_to_default_instance(
    monkeypatch, user_root
):
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

    result = sigil_resolver.resolve_sigils('[NODE="[USR=missing.email]".role]')

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
def test_resolve_sigils_table_driven_manager_method_dispatch(
    user_root, token, assertion, monkeypatch
):
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
    expected_value = (
        expected if isinstance(expected, str) else expected(baseline_total, user_ids)
    )
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

    safe_roots = sigil_resolver.get_user_safe_sigil_roots()
    assert "SAFE_ROOT" in safe_roots
    assert "UNSAFE_ROOT" not in safe_roots


@pytest.mark.django_db
def test_sigil_root_prefix_persists_uppercase_and_matches_case_insensitively():
    root, _ = SigilRoot.objects.update_or_create(
        prefix="xcp",
        defaults={"context_type": SigilRoot.Context.REQUEST},
    )
    fetched = SigilRoot.objects.get(prefix__iexact="XCP")

    assert root.prefix == "XCP"
    assert fetched.pk == root.pk


@pytest.mark.django_db
def test_get_user_safe_sigil_actions_requires_safe_entity_root():
    SigilRoot.objects.update_or_create(
        prefix="REQ",
        defaults={
            "context_type": SigilRoot.Context.REQUEST,
            "is_user_safe": True,
        },
    )
    assert sigil_resolver.get_user_safe_sigil_actions() == set()

    SigilRoot.objects.update_or_create(
        prefix="CP",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
            "is_user_safe": True,
        },
    )

    actions = sigil_resolver.get_user_safe_sigil_actions()
    assert "FILTER" in actions
    assert "COUNT" in actions


@pytest.mark.django_db
def test_generate_model_sigils_sets_default_user_safety_for_new_builtin_roots(
    monkeypatch,
):
    monkeypatch.setattr(
        "apps.sigils.sigil_builder.BUILTIN_SIGIL_POLICIES",
        {
            "REQ_SAFE": {
                "context_type": SigilRoot.Context.REQUEST,
                "is_user_safe": True,
            },
            "REQ_UNSAFE": {
                "context_type": SigilRoot.Context.REQUEST,
                "is_user_safe": False,
            },
        },
    )

    generate_model_sigils()

    assert SigilRoot.objects.get(prefix="REQ_SAFE").is_user_safe is True
    assert SigilRoot.objects.get(prefix="REQ_UNSAFE").is_user_safe is False


@pytest.mark.django_db
def test_generate_model_sigils_updates_existing_builtin_user_safety():
    SigilRoot.objects.update_or_create(
        prefix="REQ",
        defaults={
            "context_type": SigilRoot.Context.REQUEST,
            "is_user_safe": False,
        },
    )

    generate_model_sigils()

    assert SigilRoot.objects.get(prefix="REQ").is_user_safe is True
