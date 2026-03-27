"""Regression tests for explicit actions API endpoints and action models."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError

from apps.actions.models import DashboardAction, StaffTask
from apps.actions.staff_tasks import ensure_default_staff_tasks_exist, visible_staff_tasks_for_user
from apps.groups.constants import NETWORK_OPERATOR_GROUP_NAME
from apps.groups.models import SecurityGroup


@pytest.mark.django_db
def test_security_groups_endpoint_returns_session_user_groups(client):
    """Regression: supported groups API should list the logged-in user's groups."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="api-user", password="secret123")
    group = SecurityGroup.objects.create(name=NETWORK_OPERATOR_GROUP_NAME)
    user.groups.add(group)
    client.force_login(user)

    response = client.get("/actions/api/v1/security-groups/")

    assert response.status_code == 200
    assert response.json() == {"groups": [NETWORK_OPERATOR_GROUP_NAME]}


@pytest.mark.django_db
def test_dashboard_action_clean_rejects_unknown_internal_action():
    """Regression: dashboard actions must point to registered internal actions."""

    action = DashboardAction(
        content_type=ContentType.objects.get_for_model(SecurityGroup),
        slug="unknown-action",
        label="Unknown",
        action_name="not-registered",
    )

    with pytest.raises(ValidationError) as excinfo:
        action.clean()

    assert excinfo.value.message_dict == {
        "action_name": ["Select a supported internal action."]
    }


@pytest.mark.django_db
def test_staff_task_resolves_named_internal_action_for_visible_tasks():
    """Regression: seeded staff tasks should resolve URLs from named internal actions."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="staff-user",
        password="secret123",
        is_staff=True,
    )

    ensure_default_staff_tasks_exist()
    tasks = visible_staff_tasks_for_user(user)

    assert any(task["slug"] == "groups" and task["url"] == "/actions/api/v1/security-groups/" for task in tasks)
    assert StaffTask.objects.filter(slug="actions").exists() is False


@pytest.mark.django_db
def test_ensure_default_staff_tasks_exist_preserves_existing_seeded_task_edits():
    """Regression: reseeding should not overwrite operator changes to seeded tasks."""

    StaffTask.objects.create(
        slug="groups",
        label="Customized Groups",
        description="Customized description.",
        action_name="groups",
        order=5,
        default_enabled=False,
        staff_only=False,
        superuser_only=True,
        is_active=False,
    )

    ensure_default_staff_tasks_exist()

    task = StaffTask.objects.get(slug="groups")
    assert task.label == "Customized Groups"
    assert task.description == "Customized description."
    assert task.order == 5
    assert task.default_enabled is False
    assert task.staff_only is False
    assert task.superuser_only is True
    assert task.is_active is False
