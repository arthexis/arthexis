"""Coverage for suite feature admin sidebar widgets."""

from __future__ import annotations

import pytest

from apps.features.models import Feature
from apps.features.widgets import latest_feature_updates_widget


@pytest.mark.django_db
def test_latest_feature_updates_widget_lists_only_enabled_features() -> None:
    """Widget should show only enabled suite features as active entries."""

    active_feature = Feature.objects.create(
        slug="active-widget-feature",
        display="Active Widget Feature",
        is_enabled=True,
        metadata={"parameters": {"sample": "value"}},
    )
    Feature.objects.create(
        slug="disabled-widget-feature",
        display="Disabled Widget Feature",
        is_enabled=False,
        metadata={"parameters": {"ignored": "value"}},
    )

    payload = latest_feature_updates_widget()

    assert payload["feature_disabled_admin_url"].endswith("?is_enabled__exact=0")
    assert len(payload["features"]) == 1
    assert payload["features"][0]["feature"] == active_feature
    assert payload["features"][0]["params_count"] == 1
