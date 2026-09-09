"""Logging settings."""

import os

from utils.arthexis_paths import resolve_arthexis_paths
from utils.loggers import build_logging_settings

from .base import BASE_DIR, DEBUG

# Make the centralized Arthexis filesystem contract authoritative for log
# placement before the logging helper performs any writable-path fallback.
ARTHEXIS_PATHS = resolve_arthexis_paths(project_root=BASE_DIR)
os.environ.setdefault("ARTHEXIS_LOG_DIR", str(ARTHEXIS_PATHS.log_dir))

LOG_DIR, LOG_FILE_NAME, LOGGING = build_logging_settings(BASE_DIR, DEBUG)
