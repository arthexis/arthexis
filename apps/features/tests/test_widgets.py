"""Widget context coverage for active suite features."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.app.models import Application
from apps.features.models import Feature
from apps.features.widgets import latest_feature_updates_widget


@pytest.mark.django_db
def test_latest_feature_updates_widget_groups_active_features_by_app() -> None:
    """Enabled features should be grouped by owning app with clickable admin links."""

    billing = Application.objects.create(name="billing")
    sites = Application.objects.create(name="sites")
    unnamed = Application.objects.create(name="")

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
    unnamed_alpha = Feature.objects.create(
        slug="unnamed-alpha",
        display="Unnamed Alpha",
        is_enabled=True,
        main_app=unnamed,
    )

    context = latest_feature_updates_widget()

    assert [entry["app_name"] for entry in context["app_entries"]] == [
        "Unassigned",
        "",
        "billing",
        "sites",
    ]
    assert context["app_entries"][0]["features"] == [
        {
            "display": unassigned.display,
            "admin_url": reverse("admin:features_feature_change", args=[unassigned.pk]),
        }
    ]
    assert context["app_entries"][1]["features"] == [
        {
            "display": unnamed_alpha.display,
            "admin_url": reverse(
                "admin:features_feature_change",
                args=[unnamed_alpha.pk],
            ),
        }
    ]
    assert context["app_entries"][2]["features"] == [
        {
            "display": billing_alpha.display,
            "admin_url": reverse(
                "admin:features_feature_change",
                args=[billing_alpha.pk],
            ),
        }
    ]
    assert context["app_entries"][3]["features"] == [
        {
            "display": sites_alpha.display,
            "admin_url": reverse("admin:features_feature_change", args=[sites_alpha.pk]),
        }
    ]
