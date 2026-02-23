"""Regression tests for remote actions API endpoints."""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

from apps.actions.models import RemoteAction, RemoteActionToken
from apps.actions.openapi import build_openapi_spec
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


@pytest.mark.django_db
def test_invoke_action_rejects_invalid_argument_shapes(client):
    """Security: reject payloads that do not provide list args and object kwargs."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="owner2", password="secret123")
    recipe = Recipe.objects.create(
        user=user,
        display="Echo",
        slug="echo-recipe-2",
        script="result = kwargs.get('message', 'none')",
    )
    RemoteAction.objects.create(
        user=user,
        display="Echo action",
        slug="echo-action-2",
        operation_id="echoAction2",
        recipe=recipe,
    )
    _, raw_key = RemoteActionToken.issue_for_user(user)

    response = client.post(
        "/actions/api/v1/remote/echo-action-2/",
        data='{"args": {"not": "a-list"}}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )

    assert response.status_code == 400


@pytest.mark.django_db
def test_authenticate_bearer_throttles_last_used_updates(client):
    """Regression: repeat requests within one minute do not re-write last_used_at."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="throttle", password="secret123")
    _, raw_key = RemoteActionToken.issue_for_user(user)

    first = client.get(
        "/actions/api/v1/security-groups/",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert first.status_code == 200
    token = RemoteActionToken.objects.get(user=user)
    first_last_used = token.last_used_at

    second = client.get(
        "/actions/api/v1/security-groups/",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )
    assert second.status_code == 200
    token.refresh_from_db()

    assert token.last_used_at == first_last_used


@pytest.mark.django_db
def test_openapi_spec_uses_request_host_and_strips_html():
    """Regression: OpenAPI export should use deployment host and plain-text docs."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="spec-user", password="secret123")
    recipe = Recipe.objects.create(
        user=user,
        display="Recipe",
        slug="spec-recipe",
        script="result = 'ok'",
    )
    action = RemoteAction.objects.create(
        user=user,
        display="<b>Display</b>",
        slug="spec-action",
        operation_id="specAction",
        description="<script>alert(1)</script>Description",
        recipe=recipe,
    )

    request = RequestFactory().get("/admin/actions/remoteaction/my-openapi-spec/", HTTP_HOST="testserver")

    spec = build_openapi_spec(user=user, actions=[action], request=request)
    operation = spec["paths"]["/actions/api/v1/remote/spec-action/"]["post"]

    assert spec["servers"][0]["url"] == "http://testserver"
    assert operation["summary"] == "Display"
    assert operation["description"] == "alert(1)Description"
