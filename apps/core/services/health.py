"""Shared health-check primitives and registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from importlib import import_module

from django.core.management.base import CommandError

from apps.core.services.health_reporting import (
    report_health_check_failure,
    report_health_check_recovery,
)
from apps.core.services.profile_apps import profile_skip_reason

HealthCheckRunner = Callable[..., None] | str


class HealthExitCode(IntEnum):
    """Standardized process exit codes for health checks."""

    OK = 0
    CHECK_FAILED = 1
    INVALID_TARGET = 2


@dataclass(frozen=True)
class HealthCheckDefinition:
    """Metadata and runner callable for a named health target."""

    target: str
    group: str
    description: str
    runner: HealthCheckRunner
    include_in_group: bool = True
    app_selector: str | None = None
    node_roles: tuple[str, ...] = ()


def _resolve_runner(runner: HealthCheckRunner) -> Callable[..., None]:
    if not isinstance(runner, str):
        return runner

    module_name, attr_name = runner.rsplit(".", maxsplit=1)
    return getattr(import_module(module_name), attr_name)


def resolve_targets(
    *,
    available_targets: dict[str, HealthCheckDefinition],
    targets: list[str],
    groups: list[str],
) -> tuple[list[HealthCheckDefinition], list[str]]:
    """Resolve selected health check definitions and unknown selectors."""

    selected: list[HealthCheckDefinition] = []
    unknown: list[str] = []

    seen_targets: set[str] = set()
    for target in targets:
        definition = available_targets.get(target)
        if definition is None:
            unknown.append(target)
            continue
        if target in seen_targets:
            continue
        selected.append(definition)
        seen_targets.add(target)

    group_names = sorted(
        {definition.group for definition in available_targets.values()}
    )
    for group in groups:
        if group not in group_names:
            unknown.append(group)
            continue
        for definition in sorted(
            available_targets.values(), key=lambda item: item.target
        ):
            if definition.group != group or not definition.include_in_group:
                continue
            if definition.target in seen_targets:
                continue
            selected.append(definition)
            seen_targets.add(definition.target)

    return selected, unknown


def run_health_checks(
    *,
    definitions: list[HealthCheckDefinition],
    stdout,
    stderr,
    style,
    options: dict,
) -> HealthExitCode:
    """Execute health checks and return standardized exit codes."""

    if not definitions:
        return HealthExitCode.INVALID_TARGET

    report_github = bool(options.get("report_github"))
    command_texts = options.get("health_commands") or {}
    if not isinstance(command_texts, dict):
        command_texts = {}
    has_failures = False
    for definition in definitions:
        command_text = str(
            command_texts.get(definition.target)
            or options.get("health_command")
            or "manage.py health"
        )
        if skip_reason := profile_skip_reason(
            app_selector=definition.app_selector,
            node_roles=definition.node_roles,
        ):
            stdout.write(style.WARNING(f"[skipped] {definition.target}: {skip_reason}"))
            continue

        stdout.write(
            style.MIGRATE_HEADING(f"[{definition.target}] {definition.description}")
        )
        try:
            runner = _resolve_runner(definition.runner)
            runner(stdout=stdout, stderr=stderr, style=style, **options)
        except CommandError as exc:
            has_failures = True
            failure_message = str(exc)
            stderr.write(style.ERROR(failure_message))
            if report_github:
                report_health_check_failure(
                    definition=definition,
                    failure_message=failure_message,
                    command_text=command_text,
                )
        except Exception as exc:  # pragma: no cover - unexpected failures
            has_failures = True
            failure_message = f"Unexpected failure in {definition.target}: {exc}"
            stderr.write(
                style.ERROR(failure_message)
            )
            if report_github:
                report_health_check_failure(
                    definition=definition,
                    failure_message=failure_message,
                    command_text=command_text,
                )
        else:
            if report_github:
                report_health_check_recovery(
                    definition=definition,
                    command_text=command_text,
                )

    if has_failures:
        return HealthExitCode.CHECK_FAILED
    return HealthExitCode.OK
