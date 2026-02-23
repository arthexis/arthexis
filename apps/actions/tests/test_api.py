"""Regression tests for remote actions API endpoints."""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.actions.models import RemoteAction, RemoteActionToken
from apps.groups.models import SecurityGroup
from apps.recipes.models import Recipe


@pytest.mark.django_db
def test_security_groups_endpoint_returns_authorized_user_groups(client):
    """Regression: bearer-auth endpoint exposes groups for token owner only."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="api-user", password="secret123")
    group = SecurityGroup.objects.create(name="Operators")
    user.groups.add(group)
    _, raw_key = RemoteActionToken.issue_for_user(user)

    response = client.get(
        "/actions/api/v1/security-groups/",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )

    assert response.status_code == 200
    assert response.json() == {"groups": ["Operators"]}


@pytest.mark.django_db
def test_invoke_action_executes_linked_recipe_for_owned_action(client):
    """Regression: API invocation executes selected action recipe and returns result."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="owner", password="secret123")
    recipe = Recipe.objects.create(
        user=user,
        display="Echo",
        slug="echo-recipe",
        script="result = kwargs.get('message', 'none')",
    )
    RemoteAction.objects.create(
        user=user,
        display="Echo action",
        slug="echo-action",
        operation_id="echoAction",
        recipe=recipe,
    )
    _, raw_key = RemoteActionToken.issue_for_user(user)

    response = client.post(
        "/actions/api/v1/remote/echo-action/",
        data='{"kwargs": {"message": "hello"}}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )

    assert response.status_code == 200
    assert response.json() == {"action": "echo-action", "result": "hello"}


@pytest.mark.django_db
def test_expired_token_is_rejected(client):
    """Regression: expired bearer tokens cannot call remote actions APIs."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="expired", password="secret123")
    _, raw_key = RemoteActionToken.issue_for_user(
        user,
        expires_at=timezone.now() - datetime.timedelta(minutes=1),
    )

    response = client.get(
        "/actions/api/v1/security-groups/",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Token has expired."
