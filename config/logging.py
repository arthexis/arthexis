import sys
import os
import glob
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
            Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
            if self.stream:
                self.stream.close()
            self.stream = self._open()
        super().emit(record)

    def rotation_filename(self, default_name: str) -> str:
        """Place rotated logs inside the old log directory."""
        default_path = Path(default_name)
        return str(Path(settings.OLD_LOG_DIR) / default_path.name)

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
