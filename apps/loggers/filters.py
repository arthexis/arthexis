"""Logging filters for the Arthexis application."""

from __future__ import annotations

import logging

from config.active_app import get_active_app

from .debug import parse_debug_logging


class DebugAppFilter(logging.Filter):
    """Filter that restricts DEBUG logs to configured app names."""

    def __init__(self, debug_value: str | None = None, debug_enabled: bool = False):
        super().__init__()
        self._control = parse_debug_logging(debug_value, debug_enabled)

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != logging.DEBUG:
            return True

        app_name = getattr(record, "app_name", None) or get_active_app()
        return self._control.allows_app(app_name)
