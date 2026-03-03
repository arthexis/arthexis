from __future__ import annotations

import logging

from celery import shared_task

from apps.core.channel_metrics import emit_periodic_metrics

logger = logging.getLogger(__name__)


@shared_task(name="apps.core.tasks.heartbeat")
def heartbeat() -> None:
    """Log a simple heartbeat message and periodic channel metrics."""
    logger.info("Heartbeat task executed")
    emit_periodic_metrics()


@shared_task(bind=True, name="core.tasks.heartbeat")
def legacy_heartbeat(self) -> None:
    """Backward-compatible alias for the heartbeat task.

    Older Celery schedules may still reference ``core.tasks.heartbeat``.
    Register the legacy name so workers avoid "unregistered task" errors
    while routing through the current implementation.
    """

    request = getattr(self, "request", None)
    if request:
        logger.warning(
            "Received legacy heartbeat task; inspect scheduler and broker for stale entries",
            extra={
                "celery_id": getattr(request, "id", None),
                "delivery_info": getattr(request, "delivery_info", None),
                "origin": getattr(request, "hostname", None),
                "headers": getattr(request, "headers", None),
            },
        )

    heartbeat()
