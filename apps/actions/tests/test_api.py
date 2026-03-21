"""Regression tests for remote actions API endpoints."""

from __future__ import annotations

import datetime

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from django.utils import timezone

from apps.actions.models import DashboardAction, RemoteAction, RemoteActionToken
from apps.actions.openapi import build_openapi_spec
from apps.groups.models import SecurityGroup


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
def test_invoke_action_returns_payload_for_owned_action(client):
    """Regression: API invocation returns the accepted payload for an owned action."""

    user_model = get_user_model()
    user = user_model.objects.create_user(username="owner", password="secret123")
    RemoteAction.objects.create(
        user=user,
        display="Echo action",
        slug="echo-action",
        operation_id="echoAction",
    )
    _, raw_key = RemoteActionToken.issue_for_user(user)

    response = client.post(
        "/actions/api/v1/remote/echo-action/",
        data='{"kwargs": {"message": "hello"}}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {raw_key}",
    )

    assert response.status_code == 200
    assert response.json() == {
        "action": "echo-action",
        "args": [],
        "kwargs": {"message": "hello"},
    }


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
    RemoteAction.objects.create(
        user=user,
        display="Echo action",
        slug="echo-action-2",
        operation_id="echoAction2",
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
    action = RemoteAction.objects.create(
        user=user,
        display="<b>Display</b>",
        slug="spec-action",
        operation_id="specAction",
        description="<script>alert(1)</script>Description",
    )

    request = RequestFactory().get("/admin/actions/remoteaction/my-openapi-spec/", HTTP_HOST="testserver")

    spec = build_openapi_spec(user=user, actions=[action], request=request)
    operation = spec["paths"]["/actions/api/v1/remote/spec-action/"]["post"]

    assert spec["servers"][0]["url"] == "http://testserver"
    assert operation["summary"] == "Display"
    assert operation["description"] == "alert(1)Description"
    assert operation["requestBody"]["content"]["application/json"]["schema"]["description"] == (
        "Optional JSON arguments supplied with the remote action invocation."
    )


@pytest.mark.django_db
def test_dashboard_action_clean_rejects_post_admin_url_targets():
    """Regression: POST dashboard actions must keep URL-based execution semantics."""

    action = DashboardAction(
        content_type=ContentType.objects.get_for_model(SecurityGroup),
        slug="open-admin",
        label="Open admin",
        http_method=DashboardAction.HttpMethod.POST,
        target_type=DashboardAction.TargetType.ADMIN_URL,
        admin_url_name="admin:sites_site_changelist",
    )

    with pytest.raises(ValidationError) as excinfo:
        action.clean()

    assert excinfo.value.message_dict == {
        "http_method": ["POST actions must use an absolute or relative URL target."]
    }
