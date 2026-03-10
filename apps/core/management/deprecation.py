"""Helpers for command deprecation and compatibility shims."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from django.core.management.base import BaseCommand

CommandType = TypeVar("CommandType", bound=BaseCommand)


def absorbed_into_command(replacement_command: str) -> Callable[[type[CommandType]], type[CommandType]]:
    """Mark a deprecated management command as absorbed into a canonical command.

    Args:
        replacement_command: Canonical command string users should run instead.
    """

    def decorator(command_cls: type[CommandType]) -> type[CommandType]:
        """Attach machine-readable metadata to the decorated command class."""

        command_cls.arthexis_absorbed_command = True
        command_cls.arthexis_replacement_command = replacement_command
        return command_cls

    return decorator


def create_deprecated_command_shim(
    *,
    canonical_command: type[CommandType],
    shim_path: str,
    canonical_path: str,
) -> type[CommandType]:
    """Create a deprecated shim command class that delegates to ``canonical_command``."""

    class Command(canonical_command):
        """Backwards-compatible command shim for canonical commands."""

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


__all__ = ["absorbed_into_command", "create_deprecated_command_shim"]
