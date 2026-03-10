"""One-word command alias for ``report_startup``."""

from __future__ import annotations

from apps.core.management.commands.report_startup import Command as ReportStartupCommand


class Command(ReportStartupCommand):
    """Expose ``report_startup`` behavior under the ``startup`` command name."""
