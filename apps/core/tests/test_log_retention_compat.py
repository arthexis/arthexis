from __future__ import annotations

from apps.core.tasks import log_retention as legacy_log_retention
from apps.nodes import log_retention as node_log_retention


def test_log_retention_task_keeps_historical_wire_name():
    assert (
        legacy_log_retention.enforce_log_retention.name
        == "apps.core.tasks.log_retention.enforce_log_retention"
    )


def test_log_retention_task_delegates_to_node_owned_implementation(monkeypatch):
    expected = node_log_retention.RetentionResult(
        deleted_files=2,
        deleted_bytes=128,
        disk_percent=42.345,
        alert_sent=False,
    )
    monkeypatch.setattr(node_log_retention, "run_log_retention", lambda: expected)

    result = legacy_log_retention.enforce_log_retention.run()

    assert result == {
        "alert_sent": False,
        "deleted_bytes": 128,
        "deleted_files": 2,
        "disk_percent": 42.34,
    }
    assert legacy_log_retention.RetentionResult is node_log_retention.RetentionResult
