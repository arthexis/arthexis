"""Admin tests for summary wizard."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.summary.models import LLMSummaryConfig


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
