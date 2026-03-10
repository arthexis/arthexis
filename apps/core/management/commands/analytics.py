"""One-word command alias for ``export_usage_analytics``."""

from __future__ import annotations

from apps.core.management.commands.export_usage_analytics import Command as ExportUsageAnalyticsCommand


class Command(ExportUsageAnalyticsCommand):
    """Expose ``export_usage_analytics`` behavior under the ``analytics`` command name."""
