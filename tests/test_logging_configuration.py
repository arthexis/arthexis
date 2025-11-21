import logging

import pytest
from django.conf import settings as django_settings
from django.test import override_settings

from config import logging as project_logging
from config.logging import configure_library_loggers


def test_project_logging_limits_library_debug():
    if django_settings.DEBUG:
        pytest.skip("Project logging guard only activates with DEBUG=False.")

    loggers = django_settings.LOGGING.get("loggers", {})
    for logger_name in ("celery", "celery.app.trace", "graphviz", "graphviz._tools"):
        assert loggers[logger_name]["level"] == "INFO"
        assert loggers[logger_name]["propagate"] is True


def test_configure_library_loggers_respects_existing_levels():
    logging_config = {
        "loggers": {
            "celery": {"level": "WARNING", "propagate": False},
        }
    }

    configure_library_loggers(False, logging_config)

    assert logging_config["loggers"]["celery"]["level"] == "WARNING"
    assert logging_config["loggers"]["celery"]["propagate"] is False

    assert logging_config["loggers"]["graphviz"]["level"] == "INFO"
    assert logging_config["loggers"]["graphviz"]["propagate"] is True
    assert logging_config["loggers"]["graphviz._tools"]["level"] == "INFO"
    assert logging_config["loggers"]["graphviz._tools"]["propagate"] is True


def test_configure_library_loggers_noop_when_debug_enabled():
    logging_config: dict[str, dict] = {}

    configure_library_loggers(True, logging_config)

    assert logging_config == {}


def test_active_app_file_handler_reopens_after_deletion(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(project_logging, "get_active_app", lambda: "demo-app")

    with override_settings(LOG_DIR=log_dir):
        handler = project_logging.ActiveAppFileHandler(
            filename=str(log_dir / "placeholder.log"),
            when="midnight",
            backupCount=1,
            encoding="utf-8",
        )

        logger = logging.getLogger("test-active-app-handler")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        try:
            logger.info("first entry")

            log_file = log_dir / "demo-app.log"
            assert log_file.exists()

            log_file.unlink()
            logger.info("second entry")

            assert log_file.exists()
            content = log_file.read_text(encoding="utf-8")
            assert "second entry" in content
        finally:
            logger.removeHandler(handler)
            handler.close()
