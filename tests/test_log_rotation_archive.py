"""Tests for archive-aware log rotation handlers."""

import errno
from contextlib import closing
from pathlib import Path
import time

import pytest

from apps.loggers.rotation import ArchiveTimedRotatingFileHandler

pytestmark = pytest.mark.slow

def test_rotation_filename_uses_namer(tmp_path: Path) -> None:
    """Ensure rotation naming respects the configured namer."""

    log_path = tmp_path / "app.log"
    archive_dir = tmp_path / "archive"
    with closing(
        ArchiveTimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=1,
            encoding="utf-8",
            archive_dir=archive_dir,
            delay=True,
        )
    ) as handler:
        handler.namer = lambda name: f"{name}.gz"

        rotated = handler.rotation_filename(str(log_path) + ".2024-01-01")

        assert rotated == str(archive_dir / "app.log.2024-01-01.gz")

def test_get_files_to_delete_keeps_all_when_backup_count_zero(tmp_path: Path) -> None:
    """Ensure no log files are deleted when backupCount is zero."""

    log_path = tmp_path / "audit.log"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "audit.log.2024-01-01").write_text("old log")

    with closing(
        ArchiveTimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=0,
            encoding="utf-8",
            archive_dir=archive_dir,
            delay=True,
        )
    ) as handler:
        assert handler.getFilesToDelete() == []


def test_do_rollover_handles_permission_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ensure rollover lock conflicts reschedule rotation instead of crashing."""

    log_path = tmp_path / "locked.log"
    with closing(
        ArchiveTimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=1,
            encoding="utf-8",
            delay=True,
        )
    ) as handler:
        handler.rolloverAt = time.time() - 1
        original_rollover = handler.rolloverAt

        def raise_windows_lock_error(_: object | None = None) -> None:
            error = PermissionError(errno.EACCES, "sharing violation", str(log_path))
            error.winerror = 32
            raise error

        monkeypatch.setattr(
            "logging.handlers.TimedRotatingFileHandler.doRollover",
            raise_windows_lock_error,
        )
        monkeypatch.setattr("apps.loggers.rotation.os.name", "nt")
        handler.doRollover()

        assert handler.rolloverAt > original_rollover


def test_do_rollover_raises_non_lock_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ensure non-transient PermissionError values are surfaced."""

    log_path = tmp_path / "misconfigured.log"
    with closing(
        ArchiveTimedRotatingFileHandler(
            log_path,
            when="midnight",
            backupCount=1,
            encoding="utf-8",
            delay=True,
        )
    ) as handler:
        original_rollover = handler.rolloverAt

        def raise_generic_permission_error(_: object | None = None) -> None:
            raise PermissionError

        monkeypatch.setattr(
            "logging.handlers.TimedRotatingFileHandler.doRollover",
            raise_generic_permission_error,
        )
        monkeypatch.setattr(
            ArchiveTimedRotatingFileHandler,
            "_is_windows_lock_conflict",
            staticmethod(lambda _: False),
        )

        with pytest.raises(PermissionError):
            handler.doRollover()

        assert handler.rolloverAt == original_rollover
