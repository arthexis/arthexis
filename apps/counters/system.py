from __future__ import annotations

from typing import Any

from django.contrib import admin
from django.http import HttpRequest
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from apps.counters.models import DashboardRule


def _system_dashboard_rules_report_view(request: HttpRequest):
    rules = (
        DashboardRule.objects.select_related("content_type")
        .order_by("content_type__app_label", "content_type__model")
    )

    entries: list[dict[str, Any]] = []

    for rule in rules:
        content_type = rule.content_type
        model = content_type.model_class()

        model_name = model._meta.verbose_name if model else content_type.name

        status = DashboardRule.get_cached_value(content_type, rule.evaluate)

        entries.append(
            {
                "rule": rule,
                "model_name": model_name,
                "status": status,
                "rule_admin_url": reverse(
                    "admin:counters_dashboardrule_change", args=[rule.pk]
                ),
                "description": rule.failure_message or rule.success_message or rule.name,
            }
        )

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Dashboard Rules Report"),
            "dashboard_rule_entries": entries,
        }
    )

    return TemplateResponse(
        request, "admin/system_dashboard_rules_report.html", context
    )


def patch_admin_dashboard_rules_report_view() -> None:
    """Add the dashboard rules report admin view."""

    original_get_urls = admin.site.get_urls
    if getattr(original_get_urls, "_counters_dashboard_rules_patch", False):
        return

    def get_urls():
        urls = original_get_urls()
        custom = [
            path(
                "system/dashboard-rules-report/",
                admin.site.admin_view(_system_dashboard_rules_report_view),
                name="system-dashboard-rules-report",
            ),
        ]
        return custom + urls

    get_urls._counters_dashboard_rules_patch = True  # type: ignore[attr-defined]
    admin.site.get_urls = get_urls
