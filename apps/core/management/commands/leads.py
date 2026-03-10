"""One-word command alias for ``show_leads``."""

from __future__ import annotations

from apps.core.management.commands.show_leads import Command as ShowLeadsCommand


class Command(ShowLeadsCommand):
    """Expose ``show_leads`` behavior under the ``leads`` command name."""
