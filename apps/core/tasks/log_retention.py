from __future__ import annotations

from celery import shared_task

from apps.nodes import log_retention as node_log_retention

# Transitional Python compatibility for callers that imported these value types
# from the historical core module. The implementation itself is node-owned.
LogCandidate = node_log_retention.LogCandidate
RetentionResult = node_log_retention.RetentionResult


def _run_log_retention() -> RetentionResult:
    """Compatibility bridge to the node-owned retention implementation."""

    return node_log_retention.run_log_retention()


@shared_task(name="apps.core.tasks.log_retention.enforce_log_retention")
def enforce_log_retention() -> dict[str, int | float | bool]:
    """Preserve the historical Celery wire name while delegating to nodes."""

    result = node_log_retention.run_log_retention()
    return {
        "alert_sent": result.alert_sent,
        "deleted_bytes": result.deleted_bytes,
        "deleted_files": result.deleted_files,
        "disk_percent": round(result.disk_percent, 2),
    }
