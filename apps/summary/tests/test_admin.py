from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.summary.models import LLMSummaryConfig


@pytest.mark.django_db
def test_wizard_uses_single_title_and_breadcrumbs(client) -> None:
    """Regression: wizard should avoid duplicate title headers and render breadcrumbs."""

    user_model = get_user_model()
    admin = user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="admin123",
    )
    LLMSummaryConfig.objects.get_or_create(slug="lcd-log-summary")

    client.force_login(admin)
    response = client.get(reverse("admin:summary_llmsummaryconfig_wizard"))

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert content.count("<h1>LLM Summary Model Wizard</h1>") == 1
    assert "Configure" in content
    assert "breadcrumbs" in content
