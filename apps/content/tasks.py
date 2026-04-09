"""Backward-compatible Celery task entrypoints for the content app."""

from __future__ import annotations

import logging

from celery import shared_task


logger = logging.getLogger(__name__)


@shared_task(name="apps.content.tasks.run_scheduled_web_samplers")
def run_scheduled_web_samplers() -> None:
    """No-op shim for the retired web sampler periodic task."""

    logger.info("run_scheduled_web_samplers was called after task retirement; ignoring")
