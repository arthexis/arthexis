"""Widget context coverage for active suite features."""

from __future__ import annotations

import pytest

from apps.app.models import Application
from apps.features.models import Feature
from apps.features.widgets import latest_feature_updates_widget


@pytest.mark.django_db
def test_latest_feature_updates_widget_groups_active_features_by_app() -> None:
    """Enabled features should be grouped by owning app with clickable admin links."""

    billing = Application.objects.create(name="billing")
    sites = Application.objects.create(name="sites")

    billing_alpha = Feature.objects.create(
        slug="billing-alpha",
        display="Billing Alpha",
        is_enabled=True,
        main_app=billing,
    )
    Feature.objects.create(
        slug="billing-disabled",
        display="Billing Disabled",
        is_enabled=False,
        main_app=billing,
    )
    sites_alpha = Feature.objects.create(
        slug="sites-alpha",
        display="Sites Alpha",
        is_enabled=True,
        main_app=sites,
    )
    unassigned = Feature.objects.create(
        slug="unassigned-alpha",
        display="Unassigned Alpha",
        is_enabled=True,
    )

    context = latest_feature_updates_widget()

    assert [entry["app_name"] for entry in context["app_entries"]] == [
        "Unassigned",
        "billing",
        "sites",
    ]
    assert context["app_entries"][0]["features"] == [
        {
            "display": unassigned.display,
            "admin_url": f"/admin/features/feature/{unassigned.pk}/change/",
        }
    ]
    assert context["app_entries"][1]["features"] == [
        {
            "display": billing_alpha.display,
            "admin_url": f"/admin/features/feature/{billing_alpha.pk}/change/",
        }
    ]
    assert context["app_entries"][2]["features"] == [
        {
            "display": sites_alpha.display,
            "admin_url": f"/admin/features/feature/{sites_alpha.pk}/change/",
        }
    ]
