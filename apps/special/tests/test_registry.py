"""Tests for special command introspection, validation, and invocation."""

from __future__ import annotations

import pytest
from django.core.management import BaseCommand

from apps.special.models import SpecialCommand, SpecialCommandParameter
from apps.special.registry import (
    SpecialCommandValidationError,
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

@special_command(singular="nestedoptional", plural="nestedoptionals")
class NestedOptionalPositionalCommand(BaseCommand):
    """Nested parser command with optional leaf positionals that should be ignored."""

    def add_arguments(self, parser) -> None:
        subparsers = parser.add_subparsers(dest="action")
        tail_parser = subparsers.add_parser("tail")
        tail_parser.add_argument("count", type=int, nargs="?", default=20)

        rename_parser = subparsers.add_parser("rename")
        rename_parser.add_argument("name", nargs="?", default="")

    def handle(self, *args, **options) -> None:
        return None


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
def test_sync_special_command_rejects_optional_positionals() -> None:
    """Optional positional parser actions are rejected during sync."""

    with pytest.raises(SpecialCommandValidationError, match="Optional positional"):
        sync_special_command(
            command_name="optionals",
            command_cls=OptionalPositionalCommand,
        )


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
def test_sync_special_command_skips_optional_nested_positionals() -> None:
    """Nested optional positionals should not block sync or be persisted."""

    special = sync_special_command(
        command_name="nestedoptional",
        command_cls=NestedOptionalPositionalCommand,
    )

    parameter_names = {parameter.name for parameter in special.parameters.all()}

    assert parameter_names == {"action"}
