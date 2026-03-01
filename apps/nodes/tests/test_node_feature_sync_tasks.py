from django.contrib import messages

import pytest

from apps.nodes.models import Node


@pytest.mark.django_db
def test_sync_feature_tasks_disables_screenshots_when_feature_record_missing(monkeypatch):
    node = Node.objects.create(hostname="sync-local", public_endpoint="sync-local")

    monkeypatch.setattr(Node, "is_local", property(lambda self: True))
    monkeypatch.setattr(
        "apps.features.utils.is_suite_feature_enabled",
        lambda slug, default=True: True,
    )

    screenshot_enabled: list[bool] = []
    monkeypatch.setattr(
        Node,
        "_sync_screenshot_task",
        lambda self, enabled: screenshot_enabled.append(enabled),
    )
    monkeypatch.setattr(Node, "_sync_landing_lead_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_ocpp_session_report_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_upstream_poll_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_net_message_purge_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_node_update_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_connectivity_monitor_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_llm_summary_task", lambda self, enabled: None)

    node.sync_feature_tasks()

    assert screenshot_enabled == [False]


@pytest.mark.django_db
def test_sync_feature_tasks_scopes_screenshot_task_to_local_node(monkeypatch):
    node = Node.objects.create(hostname="sync-remote", public_endpoint="sync-remote")

    monkeypatch.setattr(Node, "is_local", property(lambda self: False))
    monkeypatch.setattr(
        "apps.features.utils.is_suite_feature_enabled",
        lambda slug, default=True: True,
    )

    calls: list[tuple[object, object]] = []

    def _fake_check(feature, node=None):
        calls.append((feature, node))
        from apps.nodes.feature_checks import FeatureCheckResult

        return FeatureCheckResult(True, "ok", messages.SUCCESS)

    monkeypatch.setattr("apps.nodes.feature_checks.feature_checks.run", _fake_check)

    screenshot_enabled: list[bool] = []
    monkeypatch.setattr(
        Node,
        "_sync_screenshot_task",
        lambda self, enabled: screenshot_enabled.append(enabled),
    )
    monkeypatch.setattr(Node, "_sync_landing_lead_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_ocpp_session_report_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_upstream_poll_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_net_message_purge_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_node_update_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_connectivity_monitor_task", lambda self, enabled: None)
    monkeypatch.setattr(Node, "_sync_llm_summary_task", lambda self, enabled: None)

    node.sync_feature_tasks()

    assert screenshot_enabled == [False]
    assert calls == []
