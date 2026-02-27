"""Regression tests for Remote Action Token admin defaults and quick actions."""

from __future__ import annotations

import datetime

import pytest
from django.contrib import admin
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.actions.models import RemoteActionToken


pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.regression]


def test_remote_action_token_admin_add_defaults_to_request_user(admin_client):
    """Regression: add form defaults the owner to the logged-in admin user."""

    request = RequestFactory().get(reverse("admin:actions_remoteactiontoken_add"))
    request.user = admin_client.get(reverse("admin:index")).wsgi_request.user

    model_admin = admin.site._registry[RemoteActionToken]
    initial = model_admin.get_changeform_initial_data(request)

    assert initial["user"] == request.user.pk


def test_remote_action_token_admin_add_defaults_expiration_to_24h(admin_client):
    """Regression: add form defaults expiration around 24 hours into the future."""

    request = RequestFactory().get(reverse("admin:actions_remoteactiontoken_add"))
    request.user = admin_client.get(reverse("admin:index")).wsgi_request.user

    model_admin = admin.site._registry[RemoteActionToken]
    before = timezone.localtime(timezone.now() + datetime.timedelta(hours=24))
    initial = model_admin.get_changeform_initial_data(request)
    after = timezone.localtime(timezone.now() + datetime.timedelta(hours=24))

    assert before <= initial["expires_at"] <= after


def test_remote_action_token_generate_tool_creates_token_for_current_user(admin_client):
    """Regression: one-click generate tool issues a token for current user and redirects."""

    user = admin_client.get(reverse("admin:index")).wsgi_request.user

    response = admin_client.get(reverse("admin:actions_remoteactiontoken_generate_token"), follow=True)

    assert response.status_code == 200
    assert RemoteActionToken.objects.filter(user=user).exists()


def test_remote_action_token_dashboard_includes_generate_button(admin_client):
    """Regression: dashboard quick actions include a Generate Token shortcut."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    action_url = reverse("admin:actions_remoteactiontoken_generate_token")
    expected = f'<a class="button" href="{action_url}">Generate Token</a>'
    assert expected in response.content.decode()
