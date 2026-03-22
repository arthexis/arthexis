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
    """Run the canonical heartbeat Celery task."""

    _run_heartbeat()


@shared_task(name="core.tasks.heartbeat")
def legacy_heartbeat() -> None:
    """Run the legacy heartbeat task alias for one upgrade cycle."""

    _run_heartbeat()
