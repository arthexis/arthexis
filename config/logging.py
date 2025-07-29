import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from django.conf import settings

from .active_app import get_active_app


class ActiveAppFileHandler(TimedRotatingFileHandler):
    """File handler that writes to a file named after the active app."""

    def _current_file(self) -> Path:
        if "test" in sys.argv:
            return Path(settings.LOG_DIR) / "tests.log"
        return Path(settings.LOG_DIR) / f"{get_active_app()}.log"

    def emit(self, record: logging.LogRecord) -> None:
        current = str(self._current_file())
        if self.baseFilename != current:
            self.baseFilename = current
            if self.stream:
                self.stream.close()
            self.stream = self._open()
        super().emit(record)
