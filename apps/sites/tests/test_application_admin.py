"""Tests for application admin integrations."""

import pytest
from django.contrib import admin
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from django.urls import reverse

from apps.app.models import Application
from apps.links.models.reference import Reference
from apps.sites.admin.application_admin import ApplicationAdmin
from utils.enabled_apps_lock import get_enabled_apps_lock_path


@pytest.mark.django_db
@pytest.mark.integration
def test_application_change_page_shows_application_references(admin_client):
    """The app admin page should render links tied to the selected app."""

    application = Application.objects.create(name="core", description="Core")
    visible_reference = Reference.objects.create(
        alt_text="Core Docs",
        value="https://example.com/core-docs",
        application=application,
    )
    Reference.objects.create(
        alt_text="Other Docs",
        value="https://example.com/other-docs",
    )

    url = reverse("admin:app_application_change", args=[application.pk])
    response = admin_client.get(url)

    assert response.status_code == 200
    content = response.content.decode()
    assert visible_reference.alt_text in content
    assert visible_reference.value in content
    assert "Other Docs" not in content


@pytest.mark.django_db
@pytest.mark.regression
def test_application_admin_warns_about_restart(settings):
    """Regression: app updates should warn staff that restart is required."""

    request = RequestFactory().get("/admin/app/application/")
    setattr(request, "session", {})
    setattr(request, "_messages", FallbackStorage(request))

    admin_instance = ApplicationAdmin(Application, admin.site)
    admin_instance._notify_restart_required(request, using="default")

    messages = [str(message) for message in request._messages]
    lock_path = get_enabled_apps_lock_path(settings.BASE_DIR)
    assert any("Application enablement changes are written" in message for message in messages)
    assert any(str(lock_path) in message for message in messages)
