"""Logging handlers scoped to the active Arthexis application."""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from django.conf import settings

from config.active_app import get_active_app


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
        should_reopen = self.baseFilename != current
        if self.stream and not os.path.exists(self.baseFilename):
            should_reopen = True

        if should_reopen:
            self.baseFilename = current
            Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
            if self.stream:
                self.stream.close()
            self.stream = self._open()
        try:
            super().emit(record)
        finally:
            if self.stream and not self.stream.closed:
                self.stream.close()
                self.stream = None


class ErrorFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing application errors."""

    def _current_file(self) -> Path:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        if "test" in sys.argv:
            return log_dir / "tests-error.log"
        return log_dir / "error.log"


class CeleryFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing Celery output."""

    def _current_file(self) -> Path:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        if "test" in sys.argv:
            return log_dir / "tests-celery.log"
        return log_dir / "celery.log"


class PageMissesFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing page misses."""

    def _current_file(self) -> Path:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        if "test" in sys.argv:
            return log_dir / "tests-page_misses.log"
        return log_dir / "page_misses.log"
