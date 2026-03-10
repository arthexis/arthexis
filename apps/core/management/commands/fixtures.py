"""One-word command alias for ``update_fixtures``."""

from __future__ import annotations

from apps.core.management.commands.update_fixtures import Command as UpdateFixturesCommand


class Command(UpdateFixturesCommand):
    """Expose ``update_fixtures`` behavior under the ``fixtures`` command name."""
