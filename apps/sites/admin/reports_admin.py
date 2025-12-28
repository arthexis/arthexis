import logging
from collections import deque
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import admin
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.http import FileResponse, JsonResponse
from django.template.response import TemplateResponse
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.locals.user_data import EntityModelAdmin

from ..models import ViewHistory


logger = logging.getLogger(__name__)


@admin.register(ViewHistory)
class ViewHistoryAdmin(EntityModelAdmin):
    date_hierarchy = "visited_at"
    list_display = (
        "kind",
        "site",
        "path",
        "status_code",
        "status_text",
        "method",
        "visited_at",
    )
    list_filter = ("kind", "site", "method", "status_code")
    search_fields = ("path", "error_message", "view_name", "status_text")
    readonly_fields = (
        "kind",
        "site",
        "path",
        "method",
        "status_code",
        "status_text",
        "error_message",
        "view_name",
        "visited_at",
    )
    ordering = ("-visited_at",)
    change_list_template = "admin/pages/viewhistory/change_list.html"
    actions = ["view_traffic_graph"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="View traffic graph")
    def view_traffic_graph(self, request, queryset):
        return redirect("admin:pages_viewhistory_traffic_graph")

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "traffic-graph/",
                self.admin_site.admin_view(self.traffic_graph_view),
                name="pages_viewhistory_traffic_graph",
            ),
            path(
                "traffic-data/",
                self.admin_site.admin_view(self.traffic_data_view),
                name="pages_viewhistory_traffic_data",
            ),
        ]
        return custom + urls

    def traffic_graph_view(self, request):
        context = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Public site traffic",
            "chart_endpoint": reverse("admin:pages_viewhistory_traffic_data"),
        }
        return TemplateResponse(
            request,
            "admin/pages/viewhistory/traffic_graph.html",
            context,
        )

    def traffic_data_view(self, request):
        return JsonResponse(
            self._build_chart_data(days=self._resolve_requested_days(request))
        )

    def _resolve_requested_days(self, request, default: int = 30) -> int:
        raw_value = request.GET.get("days")
        if raw_value in (None, ""):
            return default

        try:
            days = int(raw_value)
        except (TypeError, ValueError):
            return default

        minimum = 1
        maximum = 90
        return max(minimum, min(days, maximum))

    def _build_chart_data(self, days: int = 30, max_pages: int = 8) -> dict:
        end_date = timezone.localdate()
        start_date = end_date - timedelta(days=days - 1)

        start_at = datetime.combine(start_date, time.min)
        end_at = datetime.combine(end_date + timedelta(days=1), time.min)

        if settings.USE_TZ:
            current_tz = timezone.get_current_timezone()
            start_at = timezone.make_aware(start_at, current_tz)
            end_at = timezone.make_aware(end_at, current_tz)
            trunc_expression = TruncDate("visited_at", tzinfo=current_tz)
        else:
            trunc_expression = TruncDate("visited_at")

        queryset = ViewHistory.objects.filter(
            visited_at__gte=start_at, visited_at__lt=end_at
        )

        meta = {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        }

        if not queryset.exists():
            meta["pages"] = []
            return {"labels": [], "datasets": [], "meta": meta}

        top_paths = list(
            queryset.values("path")
            .annotate(total=Count("id"))
            .order_by("-total")[:max_pages]
        )
        paths = [entry["path"] for entry in top_paths]
        meta["pages"] = paths

        labels = [
            (start_date + timedelta(days=offset)).isoformat() for offset in range(days)
        ]

        aggregates = (
            queryset.filter(path__in=paths)
            .annotate(day=trunc_expression)
            .values("day", "path")
            .order_by("day")
            .annotate(total=Count("id"))
        )

        counts: dict[str, dict[str, int]] = {
            path: {label: 0 for label in labels} for path in paths
        }
        for row in aggregates:
            day = row["day"].isoformat()
            path = row["path"]
            if day in counts.get(path, {}):
                counts[path][day] = row["total"]

        palette = [
            "#1f77b4",
            "#ff7f0e",
            "#2ca02c",
            "#d62728",
            "#9467bd",
            "#8c564b",
            "#e377c2",
            "#7f7f7f",
            "#bcbd22",
            "#17becf",
        ]
        datasets = []
        for index, path in enumerate(paths):
            color = palette[index % len(palette)]
            datasets.append(
                {
                    "label": path,
                    "data": [counts[path][label] for label in labels],
                    "borderColor": color,
                    "backgroundColor": color,
                    "fill": False,
                    "tension": 0.3,
                }
            )

        return {"labels": labels, "datasets": datasets, "meta": meta}


def _read_log_tail(path: Path, limit: int) -> str:
    """Return the last ``limit`` lines from ``path`` preserving newlines."""

    with path.open("r", encoding="utf-8") as handle:
        return "".join(deque(handle, maxlen=limit))


def log_viewer(request):
    logs_dir = Path(settings.BASE_DIR) / "logs"
    logs_exist = logs_dir.exists() and logs_dir.is_dir()
    available_logs = []
    if logs_exist:
        available_logs = sorted(
            [
                entry.name
                for entry in logs_dir.iterdir()
                if entry.is_file() and not entry.name.startswith(".")
            ],
            key=str.lower,
        )

    selected_log = request.GET.get("log", "")
    log_content = ""
    log_error = ""
    limit_options = [
        {"value": "20", "label": "20"},
        {"value": "40", "label": "40"},
        {"value": "100", "label": "100"},
        {"value": "all", "label": _("All")},
    ]
    allowed_limits = [item["value"] for item in limit_options]
    limit_choice = request.GET.get("limit", "20")
    if limit_choice not in allowed_limits:
        limit_choice = "20"
    limit_index = allowed_limits.index(limit_choice)
    download_requested = request.GET.get("download") == "1"

    if selected_log:
        if selected_log in available_logs:
            selected_path = logs_dir / selected_log
            try:
                if download_requested:
                    return FileResponse(
                        selected_path.open("rb"),
                        as_attachment=True,
                        filename=selected_log,
                    )
                if limit_choice == "all":
                    try:
                        log_content = selected_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        log_content = selected_path.read_text(
                            encoding="utf-8", errors="replace"
                        )
                else:
                    try:
                        limit_value = int(limit_choice)
                    except (TypeError, ValueError):
                        limit_value = 20
                        limit_choice = "20"
                        limit_index = allowed_limits.index(limit_choice)
                    try:
                        log_content = _read_log_tail(selected_path, limit_value)
                    except UnicodeDecodeError:
                        with selected_path.open(
                            "r", encoding="utf-8", errors="replace"
                        ) as handle:
                            log_content = "".join(deque(handle, maxlen=limit_value))
            except OSError as exc:  # pragma: no cover - filesystem edge cases
                logger.warning("Unable to read log file %s", selected_path, exc_info=exc)
                log_error = _(
                    "The log file could not be read. Check server permissions and try again."
                )
        else:
            log_error = _("The requested log could not be found.")

    if not logs_exist:
        log_notice = _("The logs directory could not be found at %(path)s.") % {
            "path": logs_dir,
        }
    elif not available_logs:
        log_notice = _("No log files were found in %(path)s.") % {"path": logs_dir}
    else:
        log_notice = ""

    limit_label = limit_options[limit_index]["label"]
    context = {**admin.site.each_context(request)}
    context.update(
        {
            "title": _("Log viewer"),
            "available_logs": available_logs,
            "selected_log": selected_log,
            "log_content": log_content,
            "log_error": log_error,
            "log_notice": log_notice,
            "logs_directory": logs_dir,
            "log_limit_options": limit_options,
            "log_limit_index": limit_index,
            "log_limit_choice": limit_choice,
            "log_limit_label": limit_label,
        }
    )
    return TemplateResponse(request, "admin/log_viewer.html", context)


def get_admin_urls(original_get_urls):
    def get_urls():
        urls = original_get_urls()
        my_urls = [
            path(
                "logs/viewer/",
                admin.site.admin_view(log_viewer),
                name="log_viewer",
            ),
        ]
        return my_urls + urls

    return get_urls


admin.site.get_urls = get_admin_urls(admin.site.get_urls)
