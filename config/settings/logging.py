"""Logging settings."""

from utils.loggers import build_logging_settings

from .base import BASE_DIR, DEBUG

LOG_DIR, LOG_FILE_NAME, LOGGING = build_logging_settings(BASE_DIR, DEBUG)
