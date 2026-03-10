"""One-word command alias for ``channel_health``."""

from __future__ import annotations

from apps.core.management.commands.channel_health import Command as ChannelHealthCommand


class Command(ChannelHealthCommand):
    """Expose ``channel_health`` behavior under the ``channels`` command name."""
