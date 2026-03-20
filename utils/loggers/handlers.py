"""Logging handlers scoped to the active Arthexis application."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from django.conf import settings

from config.active_app import get_active_app

from .filenames import normalize_log_filename
from .rotation import ArchiveTimedRotatingFileHandler


def ensure_log_dir() -> Path:
    """Return the configured log directory, creating it when needed.

    Returns:
        Path: The existing log directory path.
    """

    log_dir = Path(settings.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


class ActiveAppFileHandler(ArchiveTimedRotatingFileHandler):
    """File handler that writes to a file named after the active app."""

    default_filename: str | None = None
    test_filename = "tests.log"

    def _resolve_filename(self) -> str:
        """Return the production log filename for the handler.

        Returns:
            str: The filename to use outside the test environment.

        Raises:
            ValueError: Raised when a subclass does not define a filename.
        """

        if self.default_filename is None:
            return f"{normalize_log_filename(get_active_app())}.log"
        return self.default_filename

    def _current_file(self) -> Path:
        """Return the current log file path for the handler.

        Returns:
            Path: The log file that should receive the current record.
        """

        log_dir = ensure_log_dir()
        filename = self.test_filename if "test" in sys.argv else self._resolve_filename()
        return log_dir / filename

    def _should_reopen_stream(self, current_file: str) -> bool:
        """Determine whether the handler stream should be reopened.

        Parameters:
            current_file: The absolute path for the file that should receive logs.

        Returns:
            bool: True when the underlying file handle must be reopened.
        """

        if self.baseFilename != current_file:
            return True
        return bool(self.stream and not os.path.exists(self.baseFilename))

    def emit(self, record: logging.LogRecord) -> None:
        """Write the record to the handler's current log file.

        Parameters:
            record: The log record being emitted.

        Raises:
            OSError: Propagated when the log stream cannot be opened or written.
        """

        current_file = str(self._current_file())
        if self._should_reopen_stream(current_file):
            self.baseFilename = current_file
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

    default_filename = "error.log"
    test_filename = "tests-error.log"


class CeleryFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing Celery output."""

    default_filename = "celery.log"
    test_filename = "tests-celery.log"


class PageMissesFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing page misses."""

    default_filename = "page_misses.log"
    test_filename = "tests-page_misses.log"


class CPForwarderFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing CP forwarder output."""

    default_filename = "cp_forwarder.log"
    test_filename = "tests-cp_forwarder.log"


class RFIDFileHandler(ActiveAppFileHandler):
    """File handler dedicated to capturing RFID service output."""

    default_filename = "rfid.log"
    test_filename = "tests-rfid.log"
