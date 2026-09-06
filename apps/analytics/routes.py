"""Root route provider for usage analytics."""

from django.urls import path

from .views import usage_analytics_summary

ROOT_URLPATTERNS = [
    path(
        "core/usage-analytics/summary/",
        usage_analytics_summary,
        name="usage-analytics-summary",
    ),
]
