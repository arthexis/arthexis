"""Helpers for command deprecation and compatibility shims."""

from __future__ import annotations

from typing import Any, TypeVar

from django.core.management.base import BaseCommand

CommandType = TypeVar("CommandType", bound=BaseCommand)


def create_deprecated_command_shim(
    *,
    canonical_command: type[CommandType],
    shim_path: str,
    canonical_path: str,
) -> type[CommandType]:
    """Create a deprecated shim command class that delegates to ``canonical_command``."""

    class Command(canonical_command):
        """Backwards-compatible command shim for canonical MCP commands."""

        help = (
            f"{getattr(canonical_command, 'help', '')} "
            f"[Deprecated shim: {shim_path}; canonical implementation is in {canonical_path}.]"
        )

        def handle(self, *args: Any, **options: Any) -> Any:  # type: ignore[override]
            """Emit a deprecation warning and delegate to the canonical command."""

            self.stderr.write(
                self.style.WARNING(
                    f"Deprecation warning: {shim_path} is a compatibility shim and "
                    "will be removed in a future release. "
                    f"Use the canonical command in {canonical_path}."
                )
            )
            return super().handle(*args, **options)

    return Command


__all__ = ["create_deprecated_command_shim"]
