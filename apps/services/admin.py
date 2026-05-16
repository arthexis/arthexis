from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.translation import gettext_lazy as _

from .celery_workers import CELERY_WORKERS_FEATURE_SLUG, configured_worker_count
from .lifecycle import lock_dir, read_service_name
from .models import LifecycleService


@admin.register(LifecycleService)
class LifecycleServiceAdmin(admin.ModelAdmin):
    list_display = (
        "display",
        "slug",
        "unit_template",
        "unit_kind",
        "activation",
        "suite_feature_binding",
        "feature_slug",
        "sort_order",
    )
    list_filter = ("activation", "unit_kind")
    search_fields = ("display", "slug", "unit_template", "feature_slug")
    ordering = ("sort_order", "display")
    actions = ("check_selected_statuses",)

    @admin.display(description=_("Suite feature"))
    def suite_feature_binding(self, obj: LifecycleService) -> str:
        """Return suite feature parameter mapping shown in lifecycle service changelist."""

        if obj.slug != "celery-worker":
            return ""
        return _("%(feature)s worker_count=%(count)s") % {
            "feature": CELERY_WORKERS_FEATURE_SLUG,
            "count": configured_worker_count(),
        }

    def get_urls(self):
        """Expose lifecycle status reports for selected changelist rows."""
        custom = [
            path(
                "status-report/",
                self.admin_site.admin_view(self.status_report_view),
                name="services_lifecycleservice_status_report",
            )
        ]
        return custom + super().get_urls()

    @admin.action(description=_("Check selected statuses"))
    def check_selected_statuses(self, request: HttpRequest, queryset):
        """Redirect to a report view for the selected lifecycle services."""
        selected_ids = [str(pk) for pk in queryset.values_list("pk", flat=True)]
        ids_param = ",".join(selected_ids)
        report_url = reverse("admin:services_lifecycleservice_status_report")
        return HttpResponseRedirect(f"{report_url}?ids={ids_param}")

    def status_report_view(self, request: HttpRequest):
        """Render the lifecycle status report for selected lifecycle services."""
        raw_ids = request.GET.get("ids", "")
        selected_ids: list[int] = []
        for value in raw_ids.split(","):
            try:
                selected_ids.append(int(value))
            except ValueError:
                continue

        services = list(
            LifecycleService.objects.filter(pk__in=selected_ids).order_by(
                "sort_order", "display"
            )
        )
        locks = lock_dir()
        service_name = read_service_name(locks / "service.lck")
        service_name_placeholder = service_name or "SERVICE_NAME"

        report_rows: list[dict[str, object]] = []
        for service in services:
            unit_name = service.resolved_unit_name(service_name_placeholder)
            configured = service.is_configured(
                service_name=service_name, lock_dir=locks
            )
            suite_parameter = ""
            if service.slug == "celery-worker":
                suite_parameter = self.suite_feature_binding(service)
            report_rows.append(
                {
                    "service": service,
                    "unit_name": unit_name,
                    "unit_kind": service.get_unit_kind_display(),
                    "configured": configured,
                    "service_name": service_name,
                    "suite_parameter": suite_parameter,
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": _("Lifecycle service status report"),
            "rows": report_rows,
            "selected_count": len(report_rows),
        }
        return TemplateResponse(
            request,
            "admin/services/lifecycleservice/status_report.html",
            context,
        )
