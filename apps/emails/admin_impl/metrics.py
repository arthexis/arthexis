"""Transitional imports for generic admin metrics still hosted in apps.core."""

from apps.core.admin.metrics import annotate_enabled_total, format_enabled_total

__all__ = ["annotate_enabled_total", "format_enabled_total"]
