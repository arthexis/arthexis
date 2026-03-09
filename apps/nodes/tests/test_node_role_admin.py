"""Regression tests for NodeRole admin changelist actions."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.nodes.models import Node, NodeRole


@pytest.mark.django_db
def test_noderole_changelist_marks_self_role_with_checkmark(admin_client):
    """Regression: changelist should show an indicator for the current node role."""

    role = NodeRole.objects.create(name="Constellation")
    Node.objects.create(
        hostname="self-node",
        port=8888,
        current_relation=Node.Relation.SELF,
        role=role,
    )

    response = admin_client.get(reverse("admin:nodes_noderole_changelist"))

    assert response.status_code == 200
    assert b'<th scope="col" class="column-is_assigned_to_this_node">' in response.content
    assert b"icon-yes.svg" in response.content


@pytest.mark.django_db
def test_superuser_can_switch_self_role_from_changelist_action(admin_client):
    """Regression: superusers can switch the local node role from the NodeRole changelist."""

    old_role = NodeRole.objects.create(name="Terminal")
    new_role = NodeRole.objects.create(name="Constellation")
    node = Node.objects.create(
        hostname="self-node",
        port=8888,
        current_relation=Node.Relation.SELF,
        role=old_role,
    )

    response = admin_client.post(
        reverse("admin:nodes_noderole_changelist"),
        {
            "action": "switch_selected_role",
            "_selected_action": [str(new_role.pk)],
        },
        follow=True,
    )

    node.refresh_from_db()

    assert response.status_code == 200
    assert node.role_id == new_role.pk
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert any("Switched self-node to Constellation." in message for message in messages)


@pytest.mark.django_db
def test_switch_and_restart_action_restarts_suite_service(admin_client, monkeypatch, settings):
    """Regression: switch+restart action should restart the configured suite service."""

    old_role = NodeRole.objects.create(name="Terminal")
    new_role = NodeRole.objects.create(name="Constellation")
    node = Node.objects.create(
        hostname="self-node",
        port=8888,
        current_relation=Node.Relation.SELF,
        role=old_role,
    )

    lock_dir = Path(settings.BASE_DIR) / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    (lock_dir / "service.lck").write_text("suite-demo", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(command, check, cwd, timeout):
        calls.append(command)

    monkeypatch.setattr("apps.nodes.admin.node_role_admin._systemctl_command", lambda: ["systemctl"])
    monkeypatch.setattr("apps.nodes.admin.node_role_admin.subprocess.run", fake_run)

    response = admin_client.post(
        reverse("admin:nodes_noderole_changelist"),
        {
            "action": "switch_selected_role_and_restart",
            "_selected_action": [str(new_role.pk)],
        },
        follow=True,
    )

    node.refresh_from_db()

    assert response.status_code == 200
    assert node.role_id == new_role.pk
    assert calls == [["systemctl", "restart", "suite-demo"]]
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert any("Restart requested for suite-demo." in message for message in messages)
