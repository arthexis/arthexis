"""Regression tests for per-user hidden admin dashboard apps controls."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_dashboard_defers_app_group_hydration(admin_client):
    """Dashboard should render a hydration container and load app groups asynchronously."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-dashboard-app-list-container" in content
    assert "Loading app groups..." in content
    assert reverse("admin:dashboard_app_groups") in content


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_dashboard_app_groups_endpoint_renders_hide_controls(admin_client):
    """Hydrated app groups should expose per-app hide controls."""

    response = admin_client.get(reverse("admin:dashboard_app_groups"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-dashboard-app-list" in content
    assert "data-app-visibility-toggle" in content
    assert "Show Hidden apps" in content


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_dashboard_bootstraps_client_side_hidden_app_filter(admin_client):
    """Dashboard should include client bootstrap logic for hidden app filtering."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "admin-dashboard-hidden-apps" in content
    assert "dashboard-app-visibility-bootstrap" in content
    assert "admin_dashboard_visibility.js" in content


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_app_index_does_not_render_hide_controls(admin_client):
    """Per-app index should not render hide controls without dashboard visibility JS."""

    response = admin_client.get(reverse("admin:app_list", args=("core",)))

    assert response.status_code == 200
    content = response.content.decode()
    assert "data-app-visibility-toggle" not in content
    assert "Show Hidden apps" not in content
