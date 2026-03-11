"""Introspection and safe invocation helpers for special commands."""

from __future__ import annotations

from argparse import Action
from dataclasses import dataclass
from typing import Any

from django.core.management import (
    BaseCommand,
    call_command,
    get_commands,
    load_command_class,
)
from django.db import transaction
from django.db.models import Q

from apps.special.models import SpecialCommand, SpecialCommandParameter


class SpecialCommandValidationError(ValueError):
    """Raised when a special command call does not match DB constraints."""


@dataclass(frozen=True)
class SpecialCommandDeclaration:
    """Declarative metadata attached to command classes via decorator."""

    singular: str
    plural: str
    keystone_model: str = ""


def special_command(*, singular: str, plural: str, keystone_model: str = ""):
    """Decorate a command class with special-command registration metadata."""

    def decorator(command_cls: type[BaseCommand]) -> type[BaseCommand]:
        command_cls.special_command = SpecialCommandDeclaration(
            singular=singular,
            plural=plural,
            keystone_model=keystone_model,
        )
        return command_cls

    return decorator


def _value_type_from_action(action: Action) -> str:
    if action.nargs == 0 and action.const is True:
        return SpecialCommandParameter.ValueType.BOOLEAN

    action_type = getattr(action, "type", None)
    if action_type is int:
        return SpecialCommandParameter.ValueType.INTEGER
    if action_type is float:
        return SpecialCommandParameter.ValueType.FLOAT
    return SpecialCommandParameter.ValueType.STRING


def _kind_from_action(action: Action) -> str:
    if not action.option_strings:
        return SpecialCommandParameter.ParameterKind.POSITIONAL
    if action.nargs == 0 and action.const is True:
        return SpecialCommandParameter.ParameterKind.FLAG
    return SpecialCommandParameter.ParameterKind.OPTION


def _allows_multiple(action: Action) -> bool:
    if action.nargs in ("+", "*"):
        return True
    return isinstance(action.nargs, int) and action.nargs > 1


def sync_special_command(
    *, command_name: str, command_cls: type[BaseCommand]
) -> SpecialCommand:
    """Introspect a command class and persist its safe DB definition."""

    declaration = getattr(command_cls, "special_command", None)
    if declaration is None:
        raise SpecialCommandValidationError(
            "Command class is missing @special_command declaration."
        )

    instance = command_cls()
    parser = instance.create_parser("manage.py", command_name)

    with transaction.atomic():
        has_collision = SpecialCommand.objects.filter(
            Q(name__iexact=declaration.plural) | Q(plural_name__iexact=declaration.singular)
        ).exclude(name=declaration.singular).exists()
        if has_collision:
            raise SpecialCommandValidationError(
                "Special command names and plural aliases must be globally unique."
            )

        special, _created = SpecialCommand.objects.update_or_create(
            name=declaration.singular,
            defaults={
                "plural_name": declaration.plural,
                "command_name": command_name,
                "keystone_model": declaration.keystone_model,
                "command_path": f"{command_cls.__module__}.{command_cls.__name__}",
                "is_active": True,
            },
        )

        special.parameters.all().delete()
        # NOTE: argparse does not expose a public, equivalent action-iteration API,
        # so we intentionally introspect parser._actions. If argparse changes this
        # private structure, this loop should move behind a compatibility wrapper.
        for index, action in enumerate(parser._actions):
            if action.dest in {"help"}:
                continue

            cli_name = (
                action.option_strings[-1] if action.option_strings else action.dest
            )
            choices = list(action.choices) if action.choices else []
            SpecialCommandParameter.objects.create(
                command=special,
                name=action.dest,
                cli_name=cli_name,
                kind=_kind_from_action(action),
                value_type=_value_type_from_action(action),
                is_required=bool(getattr(action, "required", False)),
                allows_multiple=_allows_multiple(action),
                choices=choices,
                help_text=(action.help or "").strip(),
                sort_order=index,
            )

    return special


def _coerce_parameter_value(parameter: SpecialCommandParameter, value: Any) -> Any:
    """Coerce a raw value into the type declared by the DB parameter definition."""

    if parameter.value_type == SpecialCommandParameter.ValueType.BOOLEAN:
        return _coerce_boolean_value(value)
    if parameter.value_type == SpecialCommandParameter.ValueType.INTEGER:
        return int(value)
    if parameter.value_type == SpecialCommandParameter.ValueType.FLOAT:
        return float(value)
    return str(value)


def _coerce_boolean_value(value: Any) -> bool:
    """Coerce a value to boolean, supporting common serialized representations."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        truthy = {"1", "true", "t", "yes", "y", "on"}
        falsy = {"0", "false", "f", "no", "n", "off"}
        if normalized in truthy:
            return True
        if normalized in falsy:
            return False
        raise ValueError(f"Invalid boolean value: {value!r}")
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"Invalid boolean value: {value!r}")


def call_special_command(name: str, /, **inputs: Any) -> Any:
    """Validate input kwargs against DB metadata and execute matching command."""

    special = SpecialCommand.objects.prefetch_related("parameters").get(
        name=name,
        is_active=True,
    )
    parameter_map = {
        parameter.name: parameter for parameter in special.parameters.all()
    }

    unknown_keys = sorted(set(inputs) - set(parameter_map))
    if unknown_keys:
        raise SpecialCommandValidationError(
            f"Unknown parameters for special command '{name}': {', '.join(unknown_keys)}"
        )

    positional_args: list[Any] = []
    option_kwargs: dict[str, Any] = {}

    for parameter in special.parameters.all():
        if parameter.name not in inputs:
            if parameter.is_required:
                raise SpecialCommandValidationError(
                    f"Missing required parameter '{parameter.name}' for '{name}'."
                )
            continue

        try:
            normalized = _coerce_parameter_value(parameter, inputs[parameter.name])
        except (TypeError, ValueError) as exc:
            raise SpecialCommandValidationError(
                f"Invalid value for '{parameter.name}': {exc}"
            ) from exc
        if parameter.choices:
            try:
                coerced_choices = [
                    _coerce_parameter_value(parameter, choice)
                    for choice in parameter.choices
                ]
            except (TypeError, ValueError):
                coerced_choices = []

            if normalized not in coerced_choices:
                raise SpecialCommandValidationError(
                    f"Invalid value for '{parameter.name}'. Expected one of: {parameter.choices}"
                )

        if parameter.kind == SpecialCommandParameter.ParameterKind.POSITIONAL:
            positional_args.append(normalized)
        else:
            option_kwargs[parameter.name] = normalized

    return call_command(special.command_name, *positional_args, **option_kwargs)


def sync_special_command_by_name(command_name: str) -> SpecialCommand:
    """Resolve and sync a declared special command by its Django command name."""

    commands = get_commands()
    app_name = commands.get(command_name)
    if not app_name:
        raise SpecialCommandValidationError(
            f"Unknown management command: {command_name}"
        )

    command_cls = load_command_class(app_name, command_name).__class__
    return sync_special_command(command_name=command_name, command_cls=command_cls)
