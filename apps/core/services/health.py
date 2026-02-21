"""Shared health-check primitives and registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

from django.core.management.base import CommandError


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
    runner: Callable[..., None]
    include_in_group: bool = True


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

    group_names = sorted({definition.group for definition in available_targets.values()})
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

    has_failures = False
    for definition in definitions:
        stdout.write(style.MIGRATE_HEADING(f"[{definition.target}] {definition.description}"))
        try:
            definition.runner(stdout=stdout, stderr=stderr, style=style, **options)
        except CommandError as exc:
            has_failures = True
            stderr.write(style.ERROR(str(exc)))
        except Exception as exc:  # pragma: no cover - unexpected failures
            has_failures = True
            stderr.write(style.ERROR(f"Unexpected failure in {definition.target}: {exc}"))

    if has_failures:
        return HealthExitCode.CHECK_FAILED
    return HealthExitCode.OK
