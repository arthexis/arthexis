from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.features.models import Feature
from apps.features.parameters import set_feature_parameter_values
from apps.summary.constants import LLM_SUMMARY_SUITE_FEATURE_SLUG
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


@pytest.mark.django_db
def test_wizard_initial_timeout_uses_saved_suite_parameter(client) -> None:
    user_model = get_user_model()
    admin = user_model.objects.create_superuser(
        username="admin2",
        email="admin2@example.com",
        password="admin123",
    )
    LLMSummaryConfig.objects.get_or_create(slug="lcd-log-summary")
    feature, _ = Feature.objects.get_or_create(
        slug=LLM_SUMMARY_SUITE_FEATURE_SLUG,
        defaults={
            "display": "LLM Summary Suite",
            "source": Feature.Source.CUSTOM,
            "is_enabled": True,
        },
    )
    set_feature_parameter_values(feature, {"timeout_seconds": "600"})
    feature.save(update_fields=["metadata", "updated_at"])

    client.force_login(admin)
    response = client.get(reverse("admin:summary_llmsummaryconfig_wizard"))

    assert response.status_code == 200
    form = response.context["form"]
    assert form.initial["timeout_seconds"] == "600"


@pytest.mark.django_db
def test_wizard_install_honors_selected_custom_model_path(client, tmp_path) -> None:
    user_model = get_user_model()
    admin = user_model.objects.create_superuser(
        username="admin3",
        email="admin3@example.com",
        password="admin123",
    )
    config, _ = LLMSummaryConfig.objects.get_or_create(slug="lcd-log-summary")
    config.model_path = str(tmp_path / "old-model")
    config.save(update_fields=["model_path", "updated_at"])

    selected_path = tmp_path / "new-model"

    client.force_login(admin)
    response = client.post(
        reverse("admin:summary_llmsummaryconfig_wizard"),
        data={
            "model_choice": "custom",
            "model_path": str(selected_path),
            "model_command": "",
            "timeout_seconds": "240",
            "install_model": "on",
        },
    )

    assert response.status_code == 302
    config.refresh_from_db()
    assert config.model_path == str(selected_path)
    assert selected_path.exists()
