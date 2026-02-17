"""Tests for application admin integrations."""

import pytest
from django.urls import reverse

from apps.app.models import Application
from apps.links.models import Reference


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
