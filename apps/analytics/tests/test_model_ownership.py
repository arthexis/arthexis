import importlib

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.urls import resolve, reverse

from apps.analytics.models import UsageEvent
from apps.analytics.views import usage_analytics_summary


def test_usage_event_is_owned_by_analytics_without_renaming_table():
    assert UsageEvent._meta.app_label == "analytics"
    assert UsageEvent._meta.db_table == "core_usageevent"
    assert [index.name for index in UsageEvent._meta.indexes] == [
        "core_usagee_app_lab_e90cdc_idx"
    ]


def test_core_usage_event_imports_are_compatibility_aliases():
    from apps.core.models import UsageEvent as LegacyUsageEvent

    assert LegacyUsageEvent is UsageEvent
    assert importlib.import_module("apps.core.models.usage_event") is importlib.import_module(
        "apps.analytics.models"
    )
    assert importlib.import_module("apps.core.analytics") is importlib.import_module(
        "apps.analytics.analytics"
    )


def test_core_registry_no_longer_owns_usage_event():
    with pytest.raises(LookupError):
        apps.get_model("core", "UsageEvent")


@pytest.mark.django_db
def test_usage_event_content_type_uses_analytics_owner():
    content_type = ContentType.objects.get_for_model(UsageEvent)
    assert content_type.app_label == "analytics"
    assert content_type.model == "usageevent"
    assert not ContentType.objects.filter(app_label="core", model="usageevent").exists()


def test_usage_analytics_route_is_owner_backed_without_changing_url():
    url = reverse("usage-analytics-summary")
    assert url == "/core/usage-analytics/summary/"
    assert resolve(url).func is usage_analytics_summary
