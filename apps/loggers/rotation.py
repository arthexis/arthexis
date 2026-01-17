"""Shared log rotation helpers for Arthexis logging."""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

TRANSACTIONAL_LOG_RETENTION_DAYS = 3
COMPLIANCE_LOG_RETENTION_DAYS = 90


def build_daily_rotating_file_handler(
    path: Path,
    *,
    retention_days: int = TRANSACTIONAL_LOG_RETENTION_DAYS,
    formatter: logging.Formatter | None = None,
    level: int | None = None,
) -> TimedRotatingFileHandler:
    """Return a daily-rotating file handler with a fixed retention window."""

    handler = TimedRotatingFileHandler(
        path,
        when="midnight",
        backupCount=retention_days,
        encoding="utf-8",
    )
    if formatter is not None:
        handler.setFormatter(formatter)
    if level is not None:
        handler.setLevel(level)
    return handler
