"""Tests for Django logging configuration helpers."""

from pathlib import Path

from apps.loggers import build_logging_settings


def test_celery_logs_are_routed_to_dedicated_file(tmp_path: Path) -> None:
    """Celery INFO logs should not pollute the shared error log."""

    log_dir, _log_file_name, logging_config = build_logging_settings(
        tmp_path, debug_enabled=False
    )

    celery_handler = logging_config["handlers"].get("celery_file")
    assert celery_handler is not None
    assert celery_handler["filename"] == str(log_dir / "celery.log")
    assert celery_handler["level"] == "INFO"

    celery_logger = logging_config["loggers"].get("celery")
    assert celery_logger is not None
    assert celery_logger["handlers"] == ["celery_file", "error_file"]
    assert celery_logger["propagate"] is False


def test_page_misses_use_dedicated_file(tmp_path: Path) -> None:
    """Page miss logs should be routed to their own handler."""

    log_dir, _log_file_name, logging_config = build_logging_settings(
        tmp_path, debug_enabled=False
    )

    handler = logging_config["handlers"].get("page_misses_file")
    assert handler is not None
    assert handler["filename"] == str(log_dir / "page_misses.log")
    assert handler["level"] == "INFO"

    logger = logging_config["loggers"].get("page_misses")
    assert logger is not None
    assert logger["handlers"] == ["page_misses_file"]
    assert logger["propagate"] is False
