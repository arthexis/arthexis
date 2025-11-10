import sys
import os
import glob
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from django.conf import settings

from .active_app import get_active_app


class ActiveAppFileHandler(TimedRotatingFileHandler):
    """File handler that writes to a file named after the active app."""

    def _current_file(self) -> Path:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        if "test" in sys.argv:
            return log_dir / "tests.log"
        return log_dir / f"{get_active_app()}.log"

    def emit(self, record: logging.LogRecord) -> None:
        current = str(self._current_file())
        if self.baseFilename != current:
            self.baseFilename = current
            Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
            if self.stream:
                self.stream.close()
            self.stream = self._open()
        super().emit(record)

    def rotation_filename(self, default_name: str) -> str:
        """Place rotated logs inside the old log directory."""
        default_path = Path(default_name)
        old_log_dir = Path(settings.OLD_LOG_DIR)
        old_log_dir.mkdir(parents=True, exist_ok=True)
        return str(old_log_dir / default_path.name)

    def getFilesToDelete(self):
        """Return files to delete in the old log directory respecting backupCount."""
        if self.backupCount <= 0:
            return []
        _, base_name = os.path.split(self.baseFilename)
        files = glob.glob(os.path.join(settings.OLD_LOG_DIR, base_name + ".*"))
        files.sort()
        if len(files) <= self.backupCount:
            return []
        return files[: len(files) - self.backupCount]


class ErrorFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing application errors."""

    def _current_file(self) -> Path:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        if "test" in sys.argv:
            return log_dir / "tests-error.log"
        return log_dir / "error.log"


def configure_library_loggers(debug_enabled: bool, logging_config: dict[str, Any]) -> None:
    """Normalize noisy third-party loggers based on the DEBUG flag.

    Celery and Graphviz become very chatty at the DEBUG level. When the
    application runs with ``DEBUG=False`` we raise the effective log level for
    those libraries to ``INFO`` so routine operations do not flood the node
    logs. When ``DEBUG`` is enabled we leave the configuration untouched so
    developers can opt-in to the extra verbosity.
    """

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
