"""One-word command alias for ``show_changelog``."""

from __future__ import annotations

from apps.core.management.commands.show_changelog import Command as ShowChangelogCommand


class Command(ShowChangelogCommand):
    """Expose ``show_changelog`` behavior under the ``changelog`` command name."""
