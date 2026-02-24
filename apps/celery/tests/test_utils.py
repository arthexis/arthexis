from __future__ import annotations

from unittest.mock import Mock

import pytest
from kombu.exceptions import OperationalError

from apps.celery import utils


@pytest.mark.parametrize(
    ("helper", "task_method"),
    [
        (utils.enqueue_task, "delay"),
        (utils.schedule_task, "apply_async"),
    ],
)
def test_task_helpers_return_false_when_broker_unavailable(caplog, helper, task_method):
    """Task helpers should return False and log a warning on broker outages."""

    task = Mock(name="healthcheck")
    task.name = "demo.healthcheck"
    getattr(task, task_method).side_effect = OperationalError(
        "Error 111 connecting to localhost:6379"
    )

    with caplog.at_level("WARNING"):
        result = helper(task, require_enabled=False)

    assert result is False
    assert "broker unavailable" in caplog.text.lower()
