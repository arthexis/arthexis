"""Regression tests for Remote Action Token admin defaults and quick actions."""

from __future__ import annotations

import datetime
from html.parser import HTMLParser

import pytest
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.actions.models import RemoteActionToken
from apps.sites.templatetags.admin_extras import model_admin_actions


pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.regression]


class _LinkParser(HTMLParser):
    """Collect anchor tag attributes from rendered HTML."""

    def __init__(self):
        super().__init__()
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        self.links.append(dict(attrs))


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


def test_remote_action_token_dashboard_includes_generate_action_link(admin_client):
    """Regression: token model exposes Generate Token as a row action, not a top button."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    action_url = reverse("admin:actions_remoteactiontoken_generate_token")

    actions = model_admin_actions({"request": response.wsgi_request}, "actions", "RemoteActionToken")

    assert any(action["url"] == action_url for action in actions)

    parser = _LinkParser()
    parser.feed(response.content.decode())
    assert not any(
        link.get("href") == action_url and "button" in link.get("class", "").split()
        for link in parser.links
    )


def test_remote_action_token_dashboard_shows_generate_link_for_add_only_admin(client):
    """Regression: dashboard keeps Generate Token visible for add-only users."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="token_dashboard_add_only",
        password="test-password",
        is_staff=True,
    )
    add_permission = Permission.objects.get(codename="add_remoteactiontoken")
    user.user_permissions.add(add_permission)
    client.force_login(user)

    response = client.get(reverse("admin:index"))

    assert response.status_code == 200
    action_url = reverse("admin:actions_remoteactiontoken_generate_token")

    parser = _LinkParser()
    parser.feed(response.content.decode())
    assert any(link.get("href") == action_url for link in parser.links)


def test_remote_action_token_generate_tool_redirects_to_add_when_list_inaccessible(client):
    """Regression: quick generator redirects to add page when changelist is not viewable."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="token_creator_only",
        password="test-password",
        is_staff=True,
    )
    add_permission = Permission.objects.get(codename="add_remoteactiontoken")
    user.user_permissions.add(add_permission)
    client.force_login(user)

    response = client.get(reverse("admin:actions_remoteactiontoken_generate_token"), follow=False)

    assert response.status_code == 302
    assert response.headers["Location"] == reverse("admin:actions_remoteactiontoken_add")


def test_remote_action_dashboard_button_opens_preview_page(admin_client):
    """Regression: dashboard Actions button opens an OpenAPI preview page first."""

    response = admin_client.get(reverse("admin:actions_remoteaction_my_openapi_spec"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Preview the generated OpenAPI file before downloading it." in content
    assert "Download YAML" in content


def test_remote_action_openapi_download_requires_explicit_query_param(admin_client):
    """Regression: OpenAPI endpoint only downloads when explicitly requested."""

    response = admin_client.get(
        reverse("admin:actions_remoteaction_my_openapi_spec"),
        data={"download": "1"},
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/yaml")
    assert response.headers["Content-Disposition"] == 'attachment; filename="my-actions-openapi.yaml"'


def test_remote_action_openapi_forbidden_for_unprivileged_staff(client):
    """Regression: OpenAPI preview and download require RemoteAction view/change rights."""

    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="openapi_staff_no_remoteaction_perm",
        password="test-password",
        is_staff=True,
    )
    client.force_login(user)

    response = client.get(reverse("admin:actions_remoteaction_my_openapi_spec"))
    assert response.status_code == 403

    response = client.get(
        reverse("admin:actions_remoteaction_my_openapi_spec"),
        data={"download": "1"},
    )
    assert response.status_code == 403
