"""Tests for Django logging configuration helpers."""

import logging
import logging.config
import sys
from pathlib import Path

import pytest
from django.conf import settings

from apps.loggers import build_logging_settings

pytestmark = pytest.mark.critical

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


def test_cp_forwarder_logs_use_dedicated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP forwarder logs should be routed to their own handler."""

    log_root = tmp_path / "logs"
    monkeypatch.setenv("ARTHEXIS_LOG_DIR", str(log_root))
    log_dir, _log_file_name, logging_config = build_logging_settings(
        tmp_path, debug_enabled=False
    )
    monkeypatch.setattr(settings, "LOG_DIR", log_dir, raising=False)

    handler = logging_config["handlers"].get("cp_forwarder_file")
    assert handler is not None
    assert handler["filename"] == str(log_dir / "cp_forwarder.log")
    assert handler["level"] == "INFO"

    logger = logging_config["loggers"].get("apps.ocpp.forwarder")
    assert logger is not None
    assert logger["handlers"] == ["cp_forwarder_file", "error_file"]
    assert logger["propagate"] is False

    logging.config.dictConfig(logging_config)
    cp_logger = logging.getLogger("apps.ocpp.forwarder")
    test_message = "cp forwarder logging check"
    cp_logger.info(test_message)
    logging.shutdown()

    expected_forwarder_name = (
        "tests-cp_forwarder.log" if "test" in sys.argv else "cp_forwarder.log"
    )
    cp_forwarder_log = log_dir / expected_forwarder_name
    assert cp_forwarder_log.exists()
    log_content = cp_forwarder_log.read_text(encoding="utf-8")
    assert test_message in log_content

    main_log = log_dir / _log_file_name
    if main_log.exists():
        main_log_content = main_log.read_text(encoding="utf-8")
        assert test_message not in main_log_content
