"""Admin regression tests for suite feature workflows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.urls import reverse

from apps.features.models import Feature


@pytest.mark.django_db
def test_feature_admin_change_form_renders_source_as_readonly(admin_client):
    """Regression: source must be displayed as read-only on the change form."""

    feature = Feature.objects.create(
        slug="custom-local-feature",
        display="Custom Local Feature",
        source=Feature.Source.CUSTOM,
    )

    change_url = reverse("admin:features_feature_change", args=[feature.pk])
    response = admin_client.get(change_url)

    assert response.status_code == 200
    assert b"field-source" in response.content
    assert b'class="readonly">Custom<' in response.content


@pytest.mark.django_db
def test_feature_admin_reload_base_tool_drops_all_and_loads_fixtures(admin_client):
    """Regression: reload-base tool should clear features and load all mainstream fixtures."""

    Feature.objects.create(slug="custom-a", display="Custom A")
    Feature.objects.create(slug="custom-b", display="Custom B")

    action_url = reverse("admin:features_feature_actions", args=["reload_base"])
    fixture_paths = [
        Path("apps/features/fixtures/features__ocpp_charge_point.json"),
        Path("apps/features/fixtures/features__evergo_api_client.json"),
    ]

    with patch("apps.features.admin.FeatureAdmin._mainstream_fixture_paths", return_value=fixture_paths):
        with patch("apps.features.admin.call_command") as mock_call_command:
            response = admin_client.post(action_url, follow=True)

    assert response.status_code == 200
    assert Feature.objects.count() == 0
    assert mock_call_command.call_count == len(fixture_paths)
