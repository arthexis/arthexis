"""Focused admin regression tests for actions app."""

from __future__ import annotations

import pytest
from django.contrib.contenttypes.models import ContentType

from apps.actions.models import DashboardAction
from apps.groups.models import SecurityGroup


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
