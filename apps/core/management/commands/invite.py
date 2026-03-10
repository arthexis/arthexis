"""One-word command alias for ``send_invite``."""

from __future__ import annotations

from apps.core.management.commands.send_invite import Command as SendInviteCommand


class Command(SendInviteCommand):
    """Expose ``send_invite`` behavior under the ``invite`` command name."""
