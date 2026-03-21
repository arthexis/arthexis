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


class IgnoreStaticAssetRequestsFilter(logging.Filter):
    """Filter out noisy static asset requests from access logs."""

    def __init__(self, static_prefix: str = "/static/") -> None:
        super().__init__()
        self._static_prefix = static_prefix

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        try:
            # A typical access log is like: '"GET /path HTTP/1.1" 200 1234'
            # Parsing the path is more robust than a substring search.
            request_line = message.split('"')[1]
            path = request_line.split()[1]
            return not path.startswith(self._static_prefix)
        except IndexError:
            # Not in the expected format of an access log, so don't filter.
            return True
