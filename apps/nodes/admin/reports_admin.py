from django.core.paginator import Paginator
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..reports import (
    collect_celery_log_entries,
    collect_scheduled_tasks,
    iter_report_periods,
    resolve_period,
)


class CeleryReportAdminMixin:
    report_template = "admin/nodes/nodefeature/celery_report.html"
    report_title = _("Celery Report")
    report_url_name = "nodes_nodefeature_celery_report"
    report_url_path = "celery-report/"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                self.report_url_path,
                self.admin_site.admin_view(self.celery_report),
                name=self.report_url_name,
            )
        ]
        return custom + urls

    def celery_report(self, request):
        period = resolve_period(request.GET.get("period"))
        now = timezone.now()
        window_end = now + period.delta
        log_window_start = now - period.delta

        scheduled_tasks = collect_scheduled_tasks(now, window_end)
        log_collection = collect_celery_log_entries(log_window_start, now)

        log_paginator = Paginator(log_collection.entries, 100)
        log_page = log_paginator.get_page(request.GET.get("page"))
        query_params = request.GET.copy()
        if "page" in query_params:
            query_params.pop("page")
        base_query = query_params.urlencode()
        log_page_base = f"?{base_query}&page=" if base_query else "?page="

        period_options = [
            {
                "key": candidate.key,
                "label": candidate.label,
                "selected": candidate.key == period.key,
                "url": f"?period={candidate.key}",
            }
            for candidate in iter_report_periods()
        ]

        context = {
            **self.admin_site.each_context(request),
            "title": self.report_title,
            "period": period,
            "period_options": period_options,
            "current_time": now,
            "window_end": window_end,
            "log_window_start": log_window_start,
            "scheduled_tasks": scheduled_tasks,
            "log_entries": list(log_page.object_list),
            "log_page": log_page,
            "log_paginator": log_paginator,
            "is_paginated": log_page.has_other_pages(),
            "log_page_base": log_page_base,
            "log_sources": log_collection.checked_sources,
        }
        return TemplateResponse(
            request,
            self.report_template,
            context,
        )
