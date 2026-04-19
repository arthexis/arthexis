import logging
import os
import re
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
LOG_LEVEL_NAMES = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LEVEL_PATTERN = re.compile(r"\[(" + "|".join(LOG_LEVEL_NAMES) + r")\]")
RECENT_LOG_LINE_WINDOW = 500
VIEWER_LINE_LIMIT = 5000


def _extract_level(log_line: str) -> str:
    """Return a normalized log level parsed from a line, or UNKNOWN when absent."""

    level_match = LEVEL_PATTERN.search(log_line)
    if not level_match:
        return "UNKNOWN"
    return level_match.group(1)


def _recommended_log_stack() -> dict[str, str]:
    """Return the suggested third-party logging stack for operations dashboards."""

    return {
        "name": "Grafana Loki + Promtail",
        "summary": "Low-overhead log aggregation with dashboarding and alerting via Grafana.",
        "url": "https://grafana.com/oss/loki/",
    }




def _observability_integration_status() -> dict[str, str | bool]:
    """Return deployment-facing status for external log aggregation wiring."""

    grafana_url = os.environ.get("ARTHEXIS_GRAFANA_URL", "").strip()
    loki_url = os.environ.get("ARTHEXIS_LOKI_URL", "").strip()
    promtail_config = os.environ.get("ARTHEXIS_PROMTAIL_CONFIG", "").strip()

    configured = bool(grafana_url and loki_url and promtail_config)
    if configured:
        status_label = _("Connected")
        status_help = _(
            "Grafana, Loki, and Promtail settings are configured for this process."
        )
    else:
        status_label = _("Not configured")
        status_help = _(
            "Set ARTHEXIS_GRAFANA_URL, ARTHEXIS_LOKI_URL, and ARTHEXIS_PROMTAIL_CONFIG to activate external aggregation links."
        )

    return {
        "configured": configured,
        "status_label": str(status_label),
        "status_help": str(status_help),
        "grafana_url": grafana_url,
        "loki_url": loki_url,
        "promtail_config": promtail_config,
    }


def _build_log_dashboard(logs_dir: Path, available_logs: list[str]) -> dict[str, object]:
    """Aggregate high-level operational insights across all available log files."""

    level_totals: dict[str, int] = {level: 0 for level in LOG_LEVEL_NAMES}
    file_summaries: list[dict[str, object]] = []
    total_lines = 0

    for filename in available_logs:
        file_path = logs_dir / filename
        try:
            line_count = 0
            recent_lines: deque[str] = deque(maxlen=RECENT_LOG_LINE_WINDOW)
            with file_path.open("r", encoding="utf-8", errors="replace") as log_file:
                for line in log_file:
                    line_count += 1
                    recent_lines.append(line)
        except OSError as exc:  # pragma: no cover - filesystem edge cases
            logger.warning("Unable to aggregate log file %s", file_path, exc_info=exc)
            continue

        per_file_levels = {level: 0 for level in LOG_LEVEL_NAMES}
        for line in recent_lines:
            level = _extract_level(line)
            if level in per_file_levels:
                per_file_levels[level] += 1
                level_totals[level] += 1

        total_lines += line_count
        try:
            modified_at = datetime.fromtimestamp(file_path.stat().st_mtime)
            modified_at = timezone.make_aware(modified_at, timezone=timezone.get_current_timezone())
            modified_label = timezone.localtime(modified_at).strftime("%Y-%m-%d %H:%M:%S %Z")
        except OSError:
            modified_label = ""

        file_summaries.append(
            {
                "name": filename,
                "line_count": line_count,
                "recent_warning": per_file_levels["WARNING"],
                "recent_error": per_file_levels["ERROR"],
                "recent_critical": per_file_levels["CRITICAL"],
                "last_updated": modified_label,
            }
        )

    level_rows = [{"level": level, "count": count} for level, count in level_totals.items()]
    return {
        "total_files": len(available_logs),
        "total_lines": total_lines,
        "level_rows": level_rows,
        "file_rows": file_summaries,
    }


def _read_recent_log_content(file_path: Path, max_lines: int = VIEWER_LINE_LIMIT) -> str:
    """Read up to the last ``max_lines`` from a log file using bounded memory."""

    recent_lines: deque[str] = deque(maxlen=max_lines)
    with file_path.open("r", encoding="utf-8", errors="replace") as log_file:
        for line in log_file:
            recent_lines.append(line)
    return "".join(recent_lines)


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


def log_viewer(request):
    """Render the admin log viewer with recent log file contents."""

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

    dashboard = _build_log_dashboard(logs_dir, available_logs) if available_logs else {
        "total_files": 0,
        "total_lines": 0,
        "level_rows": [],
        "file_rows": [],
    }

    selected_log = request.GET.get("log", "")
    log_content = ""
    log_error = ""
    log_full_path = ""
    log_last_updated = ""
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
                log_content = _read_recent_log_content(selected_path)

                log_full_path = str(selected_path.resolve())
                try:
                    modified_at = datetime.fromtimestamp(selected_path.stat().st_mtime)
                    modified_at = timezone.make_aware(
                        modified_at, timezone=timezone.get_current_timezone()
                    )
                    log_last_updated = timezone.localtime(modified_at).strftime(
                        "%Y-%m-%d %H:%M:%S %Z"
                    )
                except OSError:
                    log_last_updated = ""
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
            "log_full_path": log_full_path,
            "log_last_updated": log_last_updated,
            "hide_limit_slider": True,
            "log_dashboard": dashboard,
            "recommended_stack": _recommended_log_stack(),
            "observability_status": _observability_integration_status(),
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
