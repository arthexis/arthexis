"""Focused admin regression tests for actions app."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from apps.actions.models import DashboardAction
from apps.groups.models import SecurityGroup


@pytest.mark.django_db
@pytest.mark.integration
def test_dashboard_action_admin_exposes_named_internal_action_fields(client):
    """Ensure dashboard actions are edited as named internal actions in admin."""

    user_model = get_user_model()
    user = user_model.objects.create_superuser(
        username="actions_admin",
        password="test-password",
        email="actions@example.com",
    )
    client.force_login(user)

    response = client.get(reverse("admin:actions_dashboardaction_add"))

    assert response.status_code == 200
    assert "action_name" in response.content.decode()
    assert "absolute_url" not in response.content.decode()
    assert "caller_sigil" not in response.content.decode()


@pytest.mark.django_db
def test_dashboard_action_resolves_named_internal_action_url():
    """Regression: configured dashboard actions should render registered destinations."""

    action = DashboardAction.objects.create(
        content_type=ContentType.objects.get_for_model(SecurityGroup),
        slug="groups",
        label="Groups",
        action_name="groups",
    )

    assert action.resolve_url() == "/actions/api/v1/security-groups/"
