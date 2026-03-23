"""Compatibility Celery tasks for retired content samplers."""

from __future__ import annotations

import logging

from celery import shared_task


logger = logging.getLogger(__name__)


@shared_task(name="apps.content.tasks.run_scheduled_web_samplers")
def run_scheduled_web_samplers() -> list[int]:
    """Preserve the retired sampler task name as a no-op compatibility alias.

    Returns:
        An empty list because generic web samplers have been retired.
    """

    logger.warning(
        "Ignoring retired task alias apps.content.tasks.run_scheduled_web_samplers; "
        "migrate stored schedules and triggers to dedicated collector tasks."
    )
    return []
