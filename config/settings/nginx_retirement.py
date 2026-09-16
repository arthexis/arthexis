"""Keep retired nginx migrations available without installing nginx at runtime."""

from __future__ import annotations

import sys
from collections.abc import MutableSequence, Sequence

_MIGRATION_COMMANDS = frozenset({"migrate", "makemigrations", "showmigrations"})
_MIGRATION_STATE_APPS = ("apps.certs", "apps.nginx")
_GLOBAL_OPTIONS_WITH_VALUES = {"--settings", "--pythonpath", "--verbosity", "-v"}


def _management_command_tokens(argv: Sequence[str] | None = None) -> tuple[str, ...]:
    """Return management-command tokens after Django's leading global options."""

    args = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(args):
        arg = args[index]
        if not arg.startswith("-"):
            return tuple(args[index:])

        option_name = arg.split("=", maxsplit=1)[0]
        if option_name in _GLOBAL_OPTIONS_WITH_VALUES and "=" not in arg:
            index += 2
        else:
            index += 1

    return ()


def _is_migration_management_command(argv: Sequence[str] | None = None) -> bool:
    """Return whether this process needs historical migration app state."""

    command_tokens = _management_command_tokens(argv)
    if not command_tokens:
        return False
    if command_tokens[0] in _MIGRATION_COMMANDS:
        return True
    return command_tokens[0] == "migrations"


def apply_nginx_runtime_retirement(
    installed_apps: MutableSequence[str],
    *,
    argv: Sequence[str] | None = None,
) -> None:
    """Remove nginx from runtime and expose its shell only to migration commands."""

    installed_apps[:] = [entry for entry in installed_apps if entry != "apps.nginx"]
    if not _is_migration_management_command(argv):
        return

    for app_entry in _MIGRATION_STATE_APPS:
        if app_entry not in installed_apps:
            installed_apps.append(app_entry)
