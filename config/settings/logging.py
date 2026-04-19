"""Logging settings."""

import os

from utils.loggers import build_logging_settings

from .base import BASE_DIR, DEBUG

LOG_DIR, LOG_FILE_NAME, LOGGING = build_logging_settings(BASE_DIR, DEBUG)
ARTHEXIS_GRAFANA_URL = os.environ.get("ARTHEXIS_GRAFANA_URL", "").strip()
ARTHEXIS_LOKI_URL = os.environ.get("ARTHEXIS_LOKI_URL", "").strip()
ARTHEXIS_PROMTAIL_CONFIG = os.environ.get("ARTHEXIS_PROMTAIL_CONFIG", "").strip()
