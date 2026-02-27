"""Regression tests for admin dashboard action button placement and labels."""

from __future__ import annotations

import pytest
from django.urls import reverse


pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.regression]


def test_admin_user_tools_omits_actions_spec_link(admin_client):
    """Top user-tools row should not render the My Actions Spec link."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "My Actions Spec" not in content


def test_admin_user_tools_password_label_is_shortened(admin_client):
    """Top user-tools row should show the shortened Password link label."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert '>{0}<'.format('Password') in content
    assert 'Change password' not in content


def test_admin_dashboard_actions_row_includes_actions_button(admin_client):
    """Second-row dashboard actions should include an Actions button."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    actions_url = reverse("admin:actions_remoteaction_my_openapi_spec")
    expected_button = f'<a class="button" href="{actions_url}">Actions</a>'
    assert expected_button in response.content.decode()


def test_admin_dashboard_actions_row_omits_pyxel_button(admin_client, monkeypatch):
    """Dashboard action row should no longer show the legacy Pyxel top-row button."""

    from apps.pyxel import live_stats

    monkeypatch.setattr(live_stats, "local_ip_addresses", lambda include_loopback=True: {"127.0.0.1"})
    response = admin_client.get(reverse("admin:index"), REMOTE_ADDR="127.0.0.1")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'value="Pyxel"' not in content
