"""Celery tasks for the repos app."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task


logger = logging.getLogger(__name__)


@shared_task
def report_exception_to_github(payload: dict[str, Any]) -> None:
    """Send exception context to the GitHub issue helper.

    The task is intentionally light-weight in this repository. Deployments can
    replace it with an implementation that forwards ``payload`` to the
    automation responsible for creating GitHub issues.
    """

    logger.info(
        "Queued GitHub issue report for %s", payload.get("fingerprint", "<unknown>")
    )
