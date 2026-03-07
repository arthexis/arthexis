"""Regression tests for lifecycle service admin actions."""

from __future__ import annotations

import pytest
from django.test import override_settings
from django.urls import reverse

from apps.services.models import LifecycleService


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_lifecycle_service_status_action_redirects_to_report(admin_client):
    """Regression: bulk status action redirects with selected ids in the querystring."""

    target = LifecycleService.objects.get(slug="suite")

    response = admin_client.post(
        reverse("admin:services_lifecycleservice_changelist"),
        {
            "action": "check_selected_statuses",
            "_selected_action": [str(target.pk)],
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"] == (
        f"{reverse('admin:services_lifecycleservice_status_report')}?ids={target.pk}"
    )


@pytest.mark.django_db
@override_settings(STORAGES=TEST_STORAGES)
def test_lifecycle_service_status_report_renders_selected_rows(admin_client):
    """Regression: status report view renders configured status for selected services."""

    target = LifecycleService.objects.get(slug="suite")

    response = admin_client.get(
        reverse("admin:services_lifecycleservice_status_report"),
        {"ids": str(target.pk)},
    )

    assert response.status_code == 200
    assert b"Lifecycle service status report" in response.content
    assert target.display.encode("utf-8") in response.content
    assert b"No" in response.content
