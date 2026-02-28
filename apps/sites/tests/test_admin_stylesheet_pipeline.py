"""Regression tests for shared admin stylesheet loading."""

import pytest
from django.test import override_settings
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.integration
def test_admin_index_uses_shared_base_and_global_stylesheets(admin_client):
    """Admin index should include shared base and global stylesheet links."""

    response = admin_client.get(reverse("admin:index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "core/admin_ui_framework.css" in content
    assert "sites/css/admin/base_site.css" in content


@pytest.mark.django_db
@pytest.mark.integration
@override_settings(ADMIN_APP_STYLESHEETS={"pages": "sites/css/admin/dashboard.css"})
def test_admin_app_index_loads_configured_app_stylesheet(admin_client):
    """Admin app pages should include configured per-app stylesheet links."""

    response = admin_client.get(reverse("admin:app_list", kwargs={"app_label": "pages"}))

    assert response.status_code == 200
    content = response.content.decode()
    assert "core/admin_ui_framework.css" in content
    assert "sites/css/admin/base_site.css" in content
    assert "sites/css/admin/dashboard.css" in content
