"""Forwarding synchronization tasks."""

import logging

from celery import shared_task

from apps.ocpp.forwarder import forwarder

logger = logging.getLogger(__name__)


@shared_task(name="apps.ocpp.tasks.setup_forwarders", rate_limit="12/h")
def setup_forwarders() -> int:
    """Ensure websocket connections exist for forwarded charge points."""

    connected = forwarder.sync_forwarded_charge_points()
    if not connected:
        logger.debug("Forwarding synchronization completed with no new sessions")
    return connected


@shared_task(name="apps.ocpp.tasks.push_forwarded_charge_points")
def push_forwarded_charge_points() -> int:
    """Legacy forwarding task retained for older schedules."""

    return setup_forwarders()


@shared_task(name="apps.ocpp.tasks.sync_remote_chargers")
def sync_remote_chargers() -> int:
    """Maintain the legacy task name used by older beat schedules."""

    return setup_forwarders()
