from __future__ import annotations

from unittest.mock import Mock

from kombu.exceptions import OperationalError

from apps.celery import utils


def test_enqueue_task_returns_false_when_broker_unavailable(caplog):
    """enqueue_task should return False and log a warning on broker outages."""

    task = Mock(name="healthcheck")
    task.name = "demo.healthcheck"
    task.delay.side_effect = OperationalError("Error 111 connecting to localhost:6379")

    with caplog.at_level("WARNING"):
        result = utils.enqueue_task(task, require_enabled=False)

    assert result is False
    assert "broker unavailable" in caplog.text.lower()


def test_schedule_task_returns_false_when_broker_unavailable(caplog):
    """schedule_task should return False and log a warning on broker outages."""

    task = Mock(name="report")
    task.name = "demo.report"
    task.apply_async.side_effect = OperationalError("Error 111 connecting to localhost:6379")

    with caplog.at_level("WARNING"):
        result = utils.schedule_task(task, require_enabled=False)

    assert result is False
    assert "broker unavailable" in caplog.text.lower()
