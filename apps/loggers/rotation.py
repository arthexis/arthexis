"""Shared log rotation helpers for Arthexis logging."""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

TRANSACTIONAL_LOG_RETENTION_DAYS = 3
COMPLIANCE_LOG_RETENTION_DAYS = 90


class ArchiveTimedRotatingFileHandler(TimedRotatingFileHandler):
    """Timed rotating file handler that archives rotated logs."""

    def __init__(self, *args: object, archive_dir: Path | None = None, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        base_dir = Path(self.baseFilename).parent
        self._archive_dir = archive_dir or base_dir / "archive"

    def _ensure_archive_dir(self) -> Path:
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        return self._archive_dir

    def rotation_filename(self, default_name: str) -> str:
        archive_dir = self._ensure_archive_dir()
        return str(archive_dir / Path(default_name).name)

    def getFilesToDelete(self) -> list[str]:
        archive_dir = self._ensure_archive_dir()
        dir_name = str(archive_dir)
        base_name = Path(self.baseFilename).name
        file_names = os.listdir(dir_name) if archive_dir.exists() else []
        result: list[str] = []

        if self.namer is None:
            prefix = base_name + "."
            plen = len(prefix)
            for file_name in file_names:
                if file_name[:plen] == prefix:
                    suffix = file_name[plen:]
                    if self.extMatch.fullmatch(suffix):
                        result.append(os.path.join(dir_name, file_name))
        else:
            for file_name in file_names:
                match = self.extMatch.search(file_name)
                while match:
                    dfn = self.namer(self.baseFilename + "." + match[0])
                    if os.path.basename(dfn) == file_name:
                        result.append(os.path.join(dir_name, file_name))
                        break
                    match = self.extMatch.search(file_name, match.start() + 1)

        if len(result) < self.backupCount:
            result = []
        else:
            result.sort()
            result = result[: len(result) - self.backupCount]
        return result


def build_daily_rotating_file_handler(
    path: Path,
    *,
    retention_days: int = TRANSACTIONAL_LOG_RETENTION_DAYS,
    formatter: logging.Formatter | None = None,
    level: int | None = None,
) -> TimedRotatingFileHandler:
    """Return a daily-rotating file handler with a fixed retention window."""

    handler = ArchiveTimedRotatingFileHandler(
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
