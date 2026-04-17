import io

import pytest
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.nodes.models import Node, NodeRole
from apps.sigils.models import SigilRoot
from apps.sigils.script_runtime import (
    ScriptParseError,
    clear_script_execution_cache,
    parse_script,
)


@pytest.fixture
def cp_root():
    root, _ = SigilRoot.objects.update_or_create(
        prefix="CP",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
            "is_user_safe": True,
        },
    )
    return root


@pytest.fixture(autouse=True)
def clear_script_cache():
    clear_script_execution_cache()
    yield
    clear_script_execution_cache()


@pytest.mark.django_db
def test_parse_script_normalizes_keywords_and_identifiers():
    instructions = parse_script("let cp_host = [CP:hostname=SIM-CP-1.public_endpoint]\nemit OCPP:$CP_HOST")

    assert [item.action for item in instructions] == ["LET", "EMIT"]
    assert instructions[0].identifier == "CP_HOST"


@pytest.mark.django_db
def test_parse_script_rejects_unknown_action():
    with pytest.raises(ScriptParseError, match="unsupported action `RUN`"):
        parse_script("RUN [CP.hostname]")


@pytest.mark.django_db
def test_solve_expr_text_output(cp_root):
    role = NodeRole.objects.create(name="CLI")
    Node.objects.create(
        hostname="SIM-CP-CLI-1",
        address="127.1.0.10",
        mac_address="00:11:22:33:44:61",
        port=9101,
        public_endpoint="SIM-CP-CLI-1",
        role=role,
    )
    stdout = io.StringIO()

    call_command(
        "solve",
        expr="[CP:hostname=SIM-CP-CLI-1.public_endpoint]",
        stdout=stdout,
    )

    assert stdout.getvalue().strip() == "sim-cp-cli-1"


@pytest.mark.django_db
def test_solve_file_json_output_with_let_emit(tmp_path, cp_root):
    role = NodeRole.objects.create(name="CLI-File")
    Node.objects.create(
        hostname="SIM-CP-CLI-2",
        address="127.1.0.11",
        mac_address="00:11:22:33:44:62",
        port=9102,
        public_endpoint="SIM-CP-CLI-2",
        role=role,
    )
    script = tmp_path / "cp_script.artx"
    script.write_text(
        "LET CP_HOST = [CP:hostname=SIM-CP-CLI-2.public_endpoint]\nEMIT CP:$CP_HOST",
        encoding="utf-8",
    )
    stdout = io.StringIO()

    call_command("solve", file=str(script), output="json", stdout=stdout)

    assert '"outputs": ["CP:sim-cp-cli-2"]' in stdout.getvalue()


@pytest.mark.django_db
def test_solve_user_context_blocks_disallowed_root():
    SigilRoot.objects.update_or_create(
        prefix="CP",
        defaults={
            "context_type": SigilRoot.Context.ENTITY,
            "content_type": ContentType.objects.get_for_model(Node),
            "is_user_safe": False,
        },
    )

    with pytest.raises(CommandError, match="policy error"):
        call_command(
            "solve",
            expr="[CP:hostname=SIM-CP-CLI-3.public_endpoint]",
            context="user",
        )


@pytest.mark.django_db
def test_solve_parse_error_returns_command_error(tmp_path):
    script = tmp_path / "bad_script.artx"
    script.write_text("LET MISSING_EQUALS", encoding="utf-8")

    with pytest.raises(CommandError, match="parse error"):
        call_command("solve", file=str(script))


@pytest.mark.django_db
def test_resolve_enables_cache_by_default(monkeypatch):
    calls = {"count": 0}

    def fake_resolve(text, current=None, allowed_roots=None, allowed_actions=None):
        calls["count"] += 1
        return text

    monkeypatch.setattr("apps.sigils.script_runtime.resolve_sigils", fake_resolve)

    call_command("resolve", expr="hello")
    call_command("resolve", expr="hello")

    assert calls["count"] == 1


@pytest.mark.django_db
def test_solve_disables_cache_by_default(monkeypatch):
    calls = {"count": 0}

    def fake_resolve(text, current=None, allowed_roots=None, allowed_actions=None):
        calls["count"] += 1
        return text

    monkeypatch.setattr("apps.sigils.script_runtime.resolve_sigils", fake_resolve)

    call_command("solve", expr="hello")
    call_command("solve", expr="hello")

    assert calls["count"] == 2
