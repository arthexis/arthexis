"""Regression tests for node admin action visibility."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse

from apps.nodes.models import Node


@pytest.fixture
def node_admin_user(db):
    """Create a superuser for node admin action tests."""

    user_model = get_user_model()
    return user_model.objects.create_superuser(
        username="node-admin-actions",
        email="node-admin-actions@example.com",
        password="password",
    )


@pytest.mark.django_db
def test_node_admin_does_not_register_run_task_action(node_admin_user):
    """Node admin should not expose the removed run_task action."""

    node_admin = admin.site._registry[Node]
    request = RequestFactory().get("/admin/nodes/node/")
    request.user = node_admin_user

    actions = node_admin.get_actions(request)

    assert "run_task" not in actions


@pytest.mark.django_db
def test_node_changelist_does_not_render_run_task_action(admin_client):
    """Node changelist action selector should not list the removed run-task action."""

    Node.objects.create(hostname="admin-node", public_endpoint="admin-node")

    response = admin_client.get(reverse("admin:nodes_node_changelist"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'value="run_task"' not in content
    assert "Run task" not in content
