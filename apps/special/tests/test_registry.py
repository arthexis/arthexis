"""Tests for special command introspection, validation, and invocation."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.core.management import BaseCommand

from apps.special.models import SpecialCommand
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
