"""One-word command alias for ``offline_time``."""

from __future__ import annotations

from apps.core.management.commands.offline_time import Command as OfflineTimeCommand


class Command(OfflineTimeCommand):
    """Expose ``offline_time`` behavior under the ``availability`` command name."""
