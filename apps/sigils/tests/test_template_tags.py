import pytest
from django.contrib.contenttypes.models import ContentType
from django.template import Context, Template
from django.test import RequestFactory

from apps.nodes.models import Node, NodeRole
from apps.sigils.models import SigilRenderPolicy, SigilRoot


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


@pytest.fixture
def request_root():
    root, _ = SigilRoot.objects.update_or_create(
        prefix="REQ",
        defaults={
            "context_type": SigilRoot.Context.REQUEST,
            "is_user_safe": True,
        },
    )
    return root


@pytest.mark.django_db
def test_sigil_expr_renders_allowed_user_safe_expression(settings, node_root):
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

    rendered = Template(
        '{% load sigils %}{% sigil_expr "CP:hostname:SIM-CP-1|GET:role" %}'
    ).render(Context({}))

    assert rendered == "Charging"


@pytest.mark.django_db
def test_sigil_expr_denies_non_safe_root(settings):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    SigilRoot.objects.update_or_create(
        prefix="ENV",
        defaults={
            "context_type": SigilRoot.Context.CONFIG,
            "is_user_safe": False,
        },
    )

    rendered = Template('{% load sigils %}{% sigil_expr "ENV.DO_NOT_RENDER" %}').render(
        Context({})
    )

    assert rendered == "[ENV.DO_NOT_RENDER]"


@pytest.mark.django_db
def test_sigil_expr_denies_non_safe_action(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True

    rendered = Template('{% load sigils %}{% sigil_expr "CP|UNSAFE:port" %}').render(
        Context({})
    )

    assert rendered == "[CP|UNSAFE:port]"


@pytest.mark.django_db
def test_sigil_expr_binds_request_context(request_root):
    request = RequestFactory().get("/sigils/example?foo=bar")

    rendered = Template('{% load sigils %}{% sigil_expr "REQ.query=foo" %}').render(
        Context({"request": request})
    )

    assert rendered == "bar"


@pytest.mark.django_db
def test_sigil_expr_binds_current_object_context(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Bound")
    node = Node.objects.create(
        hostname="SIM-CP-BIND",
        address="127.0.0.12",
        mac_address="00:11:22:33:44:13",
        port=9002,
        public_endpoint="SIM-CP-BIND",
        role=role,
    )

    rendered = Template('{% load sigils %}{% sigil_expr "CP.role" %}').render(
        Context({"object": node})
    )

    assert rendered == "Bound"


@pytest.mark.django_db
def test_sigil_expr_empty_mode_collapses_unresolved(settings):
    settings.SIGILS_USER_SAFE_UNRESOLVED_BEHAVIOR = ""
    policy = SigilRenderPolicy.get_solo()
    policy.unresolved_behavior = SigilRenderPolicy.UnresolvedBehavior.EMPTY
    policy.save(update_fields=["unresolved_behavior"])

    rendered = Template('{% load sigils %}{% sigil_expr "ENV.NOPE" %}').render(Context({}))

    assert rendered == ""


@pytest.mark.django_db
def test_sigil_expr_accepts_non_string_input():
    rendered = Template("{% load sigils %}{% sigil_expr value %}").render(
        Context({"value": 12345})
    )

    assert rendered == "[12345]"


@pytest.mark.django_db
def test_empty_mode_preserves_brackets_in_resolved_values(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    settings.SIGILS_USER_SAFE_UNRESOLVED_BEHAVIOR = ""
    policy = SigilRenderPolicy.get_solo()
    policy.unresolved_behavior = SigilRenderPolicy.UnresolvedBehavior.EMPTY
    policy.save(update_fields=["unresolved_behavior"])
    role = NodeRole.objects.create(name="Voltage [V]")
    Node.objects.create(
        hostname="SIM-CP-VOLT",
        address="127.0.0.15",
        mac_address="00:11:22:33:44:16",
        port=9005,
        public_endpoint="SIM-CP-VOLT",
        role=role,
    )

    rendered = Template(
        '{% load sigils %}{{ "role=[CP:hostname:SIM-CP-VOLT|GET:role] / [ENV.NOPE]"|sigils }}'
    ).render(Context({}))

    assert rendered == "role=Voltage [V] / "


@pytest.mark.django_db
def test_empty_mode_allows_none_behavior_override(settings):
    settings.SIGILS_USER_SAFE_UNRESOLVED_BEHAVIOR = None
    policy = SigilRenderPolicy.get_solo()
    policy.unresolved_behavior = SigilRenderPolicy.UnresolvedBehavior.EMPTY
    policy.save(update_fields=["unresolved_behavior"])

    rendered = Template('{% load sigils %}{% sigil_expr "ENV.NOPE" %}').render(Context({}))

    assert rendered == ""


@pytest.mark.django_db
def test_sigils_filter_resolves_existing_placeholders(settings, node_root):
    settings.SIGILS_PIPELINE_V2_ENABLED = True
    role = NodeRole.objects.create(name="Compat")
    Node.objects.create(
        hostname="SIM-CP-COMPAT",
        address="127.0.0.13",
        mac_address="00:11:22:33:44:14",
        port=9003,
        public_endpoint="SIM-CP-COMPAT",
        role=role,
    )

    rendered = Template(
        '{% load sigils %}{{ "role=[CP:hostname:SIM-CP-COMPAT|GET:role]"|sigils }}'
    ).render(Context({}))

    assert rendered == "role=Compat"
