import logging

from celery import shared_task

from apps.content.web_sampling import schedule_pending_samplers

logger = logging.getLogger(__name__)


@shared_task
def run_scheduled_web_samplers() -> list[int]:
    """Execute any web request samplers that are due."""

    executed = schedule_pending_samplers()
    if executed:
        logger.info("Executed %s scheduled web samplers", len(executed))
    return executed
