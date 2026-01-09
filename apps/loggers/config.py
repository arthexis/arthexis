"""Logging configuration helpers."""

from __future__ import annotations

import os
import socket
import sys
from pathlib import Path
from typing import Any

from .paths import select_log_dir


def configure_library_loggers(
    debug_enabled: bool, logging_config: dict[str, Any]
) -> None:
    """Normalize noisy third-party loggers based on the DEBUG flag."""

    if debug_enabled:
        return

    loggers = logging_config.setdefault("loggers", {})
    for logger_name in (
        "celery",
        "celery.app.trace",
        "graphviz",
        "graphviz._tools",
    ):
        logger_settings: dict[str, Any] = loggers.setdefault(logger_name, {})
        logger_settings.setdefault("level", "INFO")
        logger_settings.setdefault("propagate", True)


def build_logging_settings(
    base_dir: Path, debug_enabled: bool
) -> tuple[Path, str, dict[str, Any]]:
    """Return the log directory, filename, and logging configuration."""

    log_dir = select_log_dir(base_dir)
    os.environ.setdefault("ARTHEXIS_LOG_DIR", str(log_dir))
    log_file_name = "tests.log" if "test" in sys.argv else f"{socket.gethostname()}.log"

    logging_config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            }
        },
        "handlers": {
            "file": {
                "class": "apps.loggers.handlers.ActiveAppFileHandler",
                "filename": str(log_dir / log_file_name),
                "when": "midnight",
                "backupCount": 30,
                "encoding": "utf-8",
                "formatter": "standard",
            },
            "error_file": {
                "class": "apps.loggers.handlers.ErrorFileHandler",
                "filename": str(log_dir / "error.log"),
                "when": "midnight",
                "backupCount": 30,
                "encoding": "utf-8",
                "formatter": "standard",
                "level": "WARNING",
            },
            "celery_file": {
                "class": "apps.loggers.handlers.CeleryFileHandler",
                "filename": str(log_dir / "celery.log"),
                "when": "midnight",
                "backupCount": 30,
                "encoding": "utf-8",
                "formatter": "standard",
                "level": "INFO",
            },
            "page_misses_file": {
                "class": "apps.loggers.handlers.PageMissesFileHandler",
                "filename": str(log_dir / "page_misses.log"),
                "when": "midnight",
                "backupCount": 30,
                "encoding": "utf-8",
                "formatter": "standard",
                "level": "INFO",
            },
            "console": {
                "class": "logging.StreamHandler",
                "level": "ERROR",
                "formatter": "standard",
            },
        },
        "root": {
            "handlers": ["file", "error_file", "console"],
            "level": "DEBUG",
        },
    }

    celery_logger_names = (
        "celery",
        "celery.app.trace",
        "celery.beat",
        "celery.worker",
        "celery.worker.consumer",
        "celery.utils.functional",
    )

    logging_config["loggers"] = {
        logger_name: {
            "handlers": ["celery_file", "error_file"],
            "level": "INFO",
            "propagate": False,
        }
        for logger_name in celery_logger_names
    }

    logging_config["loggers"]["page_misses"] = {
        "handlers": ["page_misses_file"],
        "level": "INFO",
        "propagate": False,
    }

    configure_library_loggers(debug_enabled, logging_config)
    return log_dir, log_file_name, logging_config
