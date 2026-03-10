"""One-word command alias for ``set_env``."""

from __future__ import annotations

from apps.core.management.commands.set_env import Command as SetEnvCommand


class Command(SetEnvCommand):
    """Expose ``set_env`` behavior under the ``env`` command name."""
