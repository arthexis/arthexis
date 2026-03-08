"""Forwarding synchronization tasks."""

import logging

from celery import shared_task

from apps.ocpp.forwarder import forwarder
from apps.ocpp.forwarder_feature import ocpp_forwarder_enabled

logger = logging.getLogger(__name__)


@shared_task(name="apps.ocpp.tasks.setup_forwarders", rate_limit="12/h")
def setup_forwarders() -> int:
    """Ensure websocket connections exist for forwarded charge points."""

    if not ocpp_forwarder_enabled(default=True):
        forwarder.clear_sessions()
        logger.debug(
            "Forwarding synchronization skipped because OCPP Forwarder is disabled"
        )
        return 0

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
