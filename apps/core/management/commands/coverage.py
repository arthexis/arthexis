"""One-word command alias for ``calculate_coverage``."""

from __future__ import annotations

from apps.core.management.commands.calculate_coverage import Command as CalculateCoverageCommand


class Command(CalculateCoverageCommand):
    """Expose ``calculate_coverage`` behavior under the ``coverage`` command name."""
