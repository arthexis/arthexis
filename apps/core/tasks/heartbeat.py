from __future__ import annotations

import logging

from celery import shared_task

from apps.core.channel_metrics import emit_periodic_metrics

logger = logging.getLogger(__name__)


def _run_heartbeat() -> None:
    """Log a simple heartbeat message and emit periodic channel metrics."""

    logger.info("Heartbeat task executed")
    emit_periodic_metrics()


@shared_task(name="apps.core.tasks.heartbeat")
def heartbeat() -> None:
    """Run the heartbeat Celery task."""

    _run_heartbeat()
