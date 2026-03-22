from __future__ import annotations

import logging

from apps.content.tasks import run_scheduled_web_samplers


def test_run_scheduled_web_samplers_is_noop_compatibility_alias(caplog) -> None:
    """Retired sampler task name remains registered as a no-op compatibility alias."""

    with caplog.at_level(logging.WARNING):
        result = run_scheduled_web_samplers()

    assert result == []
    assert "apps.content.tasks.run_scheduled_web_samplers" in caplog.text
