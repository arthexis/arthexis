"""Tests for archive-aware log rotation handlers."""

from pathlib import Path

import pytest

from apps.loggers.rotation import ArchiveTimedRotatingFileHandler

pytestmark = pytest.mark.critical

def test_rotation_filename_uses_namer(tmp_path: Path) -> None:
    """Ensure rotation naming respects the configured namer."""

    log_path = tmp_path / "app.log"
    archive_dir = tmp_path / "archive"
    handler = ArchiveTimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=1,
        encoding="utf-8",
        archive_dir=archive_dir,
    )
    handler.namer = lambda name: f"{name}.gz"

    rotated = handler.rotation_filename(str(log_path) + ".2024-01-01")

    assert rotated == str(archive_dir / "app.log.2024-01-01.gz")
    handler.close()

def test_get_files_to_delete_keeps_all_when_backup_count_zero(tmp_path: Path) -> None:
    """Ensure no log files are deleted when backupCount is zero."""

    log_path = tmp_path / "audit.log"
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    (archive_dir / "audit.log.2024-01-01").write_text("old log")

    handler = ArchiveTimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=0,
        encoding="utf-8",
        archive_dir=archive_dir,
    )

    assert handler.getFilesToDelete() == []
    handler.close()
