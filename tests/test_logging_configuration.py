import pytest
from django.conf import settings as django_settings

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
