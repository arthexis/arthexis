"""Admin dashboard smoke checks were removed in favor of focused admin URL coverage."""

from __future__ import annotations

from django.urls import resolve
from django.urls import reverse


def test_admin_dashboard_coverage_is_provided_by_model_admin_url_tests(client):
    """Regression: admin index URL continues resolving to the admin site namespace."""

    admin_index_url = reverse("admin:index")
    match = resolve(admin_index_url)

    assert match.app_name == "admin"

    response = client.get(admin_index_url)

    assert response.status_code == 302
    assert reverse("admin:login") in response.url
