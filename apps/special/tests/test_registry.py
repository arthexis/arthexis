"""Tests for special command introspection, validation, and invocation."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.management import BaseCommand

from apps.special.models import SpecialCommand, SpecialCommandParameter
from apps.special.registry import (
    SpecialCommandValidationError,
    call_special_command,
    special_command,
    sync_special_command,
)


@special_command(singular="sample", plural="samples", keystone_model="core.Node")
class SampleCommand(BaseCommand):
    """Test-only command for parameter introspection and invocation checks."""

    help = "Sample special command"

    def add_arguments(self, parser) -> None:
        parser.add_argument("slug")
        parser.add_argument("--count", type=int, required=True)
        parser.add_argument("--enabled", action="store_true")
        parser.add_argument("--kind", choices=["suite", "node"])

    def handle(self, *args, **options) -> None:
        return None


@special_command(singular="optional", plural="optionals")
class OptionalPositionalCommand(BaseCommand):
    """Command with optional positional arg used to verify sync guardrails."""

    def add_arguments(self, parser) -> None:
        parser.add_argument("maybe", nargs="?")

    def handle(self, *args, **options) -> None:
        return None


@special_command(singular="nested", plural="nesteds")
class NestedSubparserCommand(BaseCommand):
    """Command with nested subparsers used to verify sync compatibility."""

    def add_arguments(self, parser) -> None:
        parser.add_argument("--shared")
        subparsers = parser.add_subparsers(dest="action")

        show_parser = subparsers.add_parser("show")
        show_parser.add_argument("slug")

        auth_parser = subparsers.add_parser("auth")
        auth_subparsers = auth_parser.add_subparsers(dest="auth_action", required=True)
        auth_set = auth_subparsers.add_parser("set")
        auth_set.add_argument("username")
        auth_set.add_argument("password")

    def handle(self, *args, **options) -> None:
        return None


@pytest.mark.django_db
def test_special_command_model_enforces_one_word_restrictions() -> None:
    """Special command names should enforce lowercase one-word restrictions."""

    command = SpecialCommand(
        name="two words",
        plural_name="samples",
        command_name="sample",
        command_path="tests.SampleCommand",
    )

    with pytest.raises(ValidationError, match="one lowercase word"):
        command.full_clean()


@pytest.mark.django_db
def test_special_command_parameter_allows_hyphenated_option_name() -> None:
    """Hyphenated long option names should pass model validation."""

    command = SpecialCommand.objects.create(
        name="sample",
        plural_name="samples",
        command_name="samples",
        command_path="tests.SampleCommand",
    )
    parameter = SpecialCommandParameter(
        command=command,
        name="ws_auth_username",
        cli_name="--ws-auth-username",
        kind=SpecialCommandParameter.ParameterKind.OPTION,
        value_type=SpecialCommandParameter.ValueType.STRING,
    )

    parameter.full_clean()


@pytest.mark.django_db
def test_sync_special_command_persists_argument_schema() -> None:
    """Sync should introspect parser actions and persist parameter definitions."""

    special = sync_special_command(command_name="sample", command_cls=SampleCommand)

    assert special.name == "sample"
    assert special.plural_name == "samples"
    assert special.command_name == "sample"
    parameter_map = {
        parameter.name: parameter for parameter in special.parameters.all()
    }

    assert parameter_map["slug"].kind == "positional"
    assert parameter_map["count"].value_type == "integer"
    assert parameter_map["count"].is_required is True
    assert parameter_map["enabled"].kind == "flag"
    assert parameter_map["kind"].choices == ["suite", "node"]
    assert parameter_map["kind"].nargs is None
    assert parameter_map["enabled"].const is True


@pytest.mark.django_db
def test_call_special_command_validates_inputs_and_forwards(monkeypatch) -> None:
    """Validated special calls should forward normalized arguments to call_command."""

    sync_special_command(command_name="samples", command_cls=SampleCommand)

    captured: dict[str, object] = {}

    def fake_call_command(name: str, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr("apps.special.registry.call_command", fake_call_command)

    result = call_special_command(
        "sample",
        slug="alpha",
        count="2",
        enabled=True,
        kind="suite",
    )

    assert result == "ok"
    assert captured["name"] == "samples"
    assert captured["args"] == ("alpha",)
    assert captured["kwargs"] == {"count": 2, "enabled": True, "kind": "suite"}


@pytest.mark.django_db
def test_call_special_command_accepts_plural_alias(monkeypatch) -> None:
    """Plural alias should resolve to the same synced special command."""

    sync_special_command(command_name="samples", command_cls=SampleCommand)

    captured: dict[str, object] = {}

    def fake_call_command(name: str, *args, **kwargs):
        captured["name"] = name
        return "ok"

    monkeypatch.setattr("apps.special.registry.call_command", fake_call_command)

    result = call_special_command("samples", slug="alpha", count=1)

    assert result == "ok"
    assert captured["name"] == "samples"


@pytest.mark.django_db
def test_sync_special_command_rejects_optional_positionals() -> None:
    """Optional positional parser actions are rejected during sync."""

    with pytest.raises(SpecialCommandValidationError, match="Optional positional"):
        sync_special_command(
            command_name="optionals",
            command_cls=OptionalPositionalCommand,
        )


@pytest.mark.django_db
def test_call_special_command_rejects_unknown_or_invalid_inputs() -> None:
    """Special command invocation should reject unknown keys and invalid choices."""

    sync_special_command(command_name="sample", command_cls=SampleCommand)

    with pytest.raises(SpecialCommandValidationError, match="Unknown parameters"):
        call_special_command("sample", slug="alpha", count=1, unexpected="x")

    with pytest.raises(SpecialCommandValidationError, match="Expected one of"):
        call_special_command("sample", slug="alpha", count=1, kind="bad")


@pytest.mark.django_db
def test_call_special_command_parses_string_booleans(monkeypatch) -> None:
    """String boolean values should be parsed without Python truthiness pitfalls."""

    sync_special_command(command_name="sample", command_cls=SampleCommand)

    captured: dict[str, object] = {}

    def fake_call_command(name: str, *args, **kwargs):
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr("apps.special.registry.call_command", fake_call_command)
    call_special_command("sample", slug="alpha", count=1, enabled="false")

    assert captured["kwargs"]["enabled"] is False

    with pytest.raises(SpecialCommandValidationError, match="Invalid boolean value"):
        call_special_command("sample", slug="alpha", count=1, enabled="not-bool")


@pytest.mark.django_db
def test_sync_special_command_excludes_global_management_options() -> None:
    """Sync should not persist Django's built-in global management options."""

    special = sync_special_command(command_name="sample", command_cls=SampleCommand)

    parameter_names = {parameter.name for parameter in special.parameters.all()}

    assert "settings" not in parameter_names
    assert "pythonpath" not in parameter_names


@pytest.mark.django_db
def test_call_special_command_rejects_legacy_global_management_option_metadata(
    monkeypatch,
) -> None:
    """Calls should reject global options even if stale rows exist in the database."""

    command = SpecialCommand.objects.create(
        name="legacy",
        plural_name="legacies",
        command_name="sample",
        command_path="tests.SampleCommand",
    )
    SpecialCommandParameter.objects.create(
        command=command,
        name="slug",
        cli_name="slug",
        kind=SpecialCommandParameter.ParameterKind.POSITIONAL,
        value_type=SpecialCommandParameter.ValueType.STRING,
        is_required=True,
        sort_order=0,
    )
    SpecialCommandParameter.objects.create(
        command=command,
        name="settings",
        cli_name="--settings",
        kind=SpecialCommandParameter.ParameterKind.OPTION,
        value_type=SpecialCommandParameter.ValueType.STRING,
        sort_order=1,
    )

    def fake_call_command(name: str, *args, **kwargs):
        return "ok"

    monkeypatch.setattr("apps.special.registry.call_command", fake_call_command)

    with pytest.raises(SpecialCommandValidationError, match="Unknown parameters"):
        call_special_command("legacy", slug="alpha", settings="malicious.settings")

@pytest.mark.django_db
def test_call_special_command_reports_unknown_command() -> None:
    """Unknown command keys should raise the public validation error type."""

    with pytest.raises(SpecialCommandValidationError, match="Unknown special command"):
        call_special_command("does-not-exist")


@pytest.mark.django_db
def test_sync_special_command_supports_nested_subparsers() -> None:
    """Sync should flatten nested subparsers without rejecting the command."""

    special = sync_special_command(command_name="nested", command_cls=NestedSubparserCommand)

    parameter_map = {parameter.name: parameter for parameter in special.parameters.all()}

    assert parameter_map["action"].kind == SpecialCommandParameter.ParameterKind.POSITIONAL
    assert parameter_map["action"].choices == ["show", "auth"]
    assert parameter_map["slug"].kind == SpecialCommandParameter.ParameterKind.POSITIONAL
    assert parameter_map["auth_action"].choices == ["set"]
    assert parameter_map["username"].kind == SpecialCommandParameter.ParameterKind.POSITIONAL
    assert parameter_map["shared"].kind == SpecialCommandParameter.ParameterKind.OPTION


@pytest.mark.django_db
def test_call_special_command_supports_nested_subparsers(monkeypatch) -> None:
    """Nested subparser selections should be forwarded in positional order."""

    sync_special_command(command_name="nested", command_cls=NestedSubparserCommand)

    captured: dict[str, object] = {}

    def fake_call_command(name: str, *args, **kwargs):
        captured["name"] = name
        captured["args"] = args
        captured["kwargs"] = kwargs
        return "ok"

    monkeypatch.setattr("apps.special.registry.call_command", fake_call_command)

    result = call_special_command(
        "nested",
        action="auth",
        auth_action="set",
        username="cp-user",
        password="secret123",
        shared="station-a",
    )

    assert result == "ok"
    assert captured["name"] == "nested"
    assert captured["args"] == ("auth", "set", "cp-user", "secret123")
    assert captured["kwargs"] == {"shared": "station-a"}
