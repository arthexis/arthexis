from __future__ import annotations

import logging
from pathlib import Path
import subprocess
from urllib.parse import parse_qsl

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, URLPattern, URLResolver, path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from apps.actions.models import StaffTask, StaffTaskPreference
from apps.actions.staff_tasks import ensure_default_staff_tasks_exist
from apps.core import changelog
from apps.core.models import AdminNotice
from apps.core.systemctl import _systemctl_command
from apps.services.lifecycle import SERVICE_NAME_LOCK, lock_dir, read_service_name
from .filesystem import _clear_auto_upgrade_skip_revisions
from .network import _upgrade_redirect
from .ui import (
    STARTUP_REPORT_DEFAULT_LIMIT,
    _build_nginx_report,
    _build_services_report,
    _build_system_fields,
    _build_uptime_report,
    _gather_info,
    _read_startup_report,
)
from .upgrade import (
    UPGRADE_CHANNEL_CHOICES,
    UPGRADE_REVISION_SESSION_KEY,
    _build_auto_upgrade_report,
    _auto_upgrade_next_check,
    _load_upgrade_revision_info,
    _trigger_upgrade_check,
)


logger = logging.getLogger(__name__)


def _system_view(request):
    ensure_default_staff_tasks_exist()
    tasks = list(StaffTask.objects.filter(is_active=True).order_by("order", "label"))
    task_pref_map = {
        pref.task_id: pref
        for pref in StaffTaskPreference.objects.filter(user=request.user, task__in=tasks)
    }

    if request.method == "POST":
        selected_task_ids = {
            int(task_id)
            for task_id in request.POST.getlist("dashboard_tasks")
            if str(task_id).isdigit()
        }
        for task in tasks:
            if task.staff_only and not request.user.is_staff:
                continue
            if task.superuser_only and not request.user.is_superuser:
                continue
            try:
                reverse(task.admin_url_name)
            except NoReverseMatch:
                continue
            enabled = task.pk in selected_task_ids
            if enabled == task.default_enabled:
                StaffTaskPreference.objects.filter(user=request.user, task=task).delete()
                continue
            StaffTaskPreference.objects.update_or_create(
                user=request.user,
                task=task,
                defaults={"is_enabled": enabled},
            )
        messages.success(request, _("Dashboard tasks updated."))
        return HttpResponseRedirect(reverse("admin:system"))

    task_rows = []
    for task in tasks:
        if task.staff_only and not request.user.is_staff:
            continue
        if task.superuser_only and not request.user.is_superuser:
            continue
        try:
            task_url = reverse(task.admin_url_name)
        except NoReverseMatch:
            continue
        pref = task_pref_map.get(task.pk)
        is_enabled = pref.is_enabled if pref is not None else task.default_enabled
        task_rows.append(
            {
                "task": task,
                "url": task_url,
                "is_enabled": is_enabled,
            }
        )

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Tasks"),
            "task_rows": task_rows,
        }
    )
    return TemplateResponse(request, "admin/system.html", context)


def _suite_service_status(base_dir: Path | None = None) -> dict[str, str | bool]:
    """Return suite service metadata and whether it is active in systemd."""

    resolved_base_dir = Path(base_dir or settings.BASE_DIR)
    service_name = read_service_name(lock_dir(resolved_base_dir) / SERVICE_NAME_LOCK)
    if not service_name:
        return {"configured": False, "service_name": "", "unit_name": "", "is_active": False}

    command = _systemctl_command()
    unit_name = f"{service_name}.service"
    if not command:
        return {
            "configured": True,
            "service_name": service_name,
            "unit_name": unit_name,
            "is_active": False,
        }

    try:
        result = subprocess.run(
            [*command, "is-active", "--quiet", unit_name],
            check=False,
            cwd=resolved_base_dir,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "configured": True,
            "service_name": service_name,
            "unit_name": unit_name,
            "is_active": False,
        }

    return {
        "configured": True,
        "service_name": service_name,
        "unit_name": unit_name,
        "is_active": result.returncode == 0,
    }



def _collect_admin_report_routes() -> list[dict[str, str]]:
    """Return reverseable admin routes that appear to be report views."""

    routes: list[dict[str, str]] = []

    def _walk(patterns: list[URLPattern | URLResolver]) -> None:
        for pattern in patterns:
            if isinstance(pattern, URLResolver):
                _walk(pattern.url_patterns)
                continue

            route_name = pattern.name
            if not route_name or "report" not in route_name:
                continue
            if route_name.endswith("-data"):
                continue

            try:
                route_url = reverse(f"admin:{route_name}")
            except NoReverseMatch:
                continue

            route_label = route_name.replace("-", " ").replace("_", " ").title()
            routes.append(
                {
                    "name": route_name,
                    "label": route_label,
                    "url": route_url,
                }
            )

    _walk(admin.site.get_urls())
    return sorted(routes, key=lambda item: item["label"])


def _system_reports_view(request):
    """Render and launch the unified report runner for admin reports."""

    report_routes = _collect_admin_report_routes()
    selected_report = request.GET.get("report", "")
    params_value = request.GET.get("params", "")

    if request.method == "POST":
        selected_report = (request.POST.get("report") or "").strip()
        params_value = (request.POST.get("params") or "").strip()

        if not selected_report:
            messages.error(request, _("Choose a report to run."))
        else:
            try:
                base_url = reverse(f"admin:{selected_report}")
            except NoReverseMatch:
                messages.error(request, _("The selected report is unavailable."))
            else:
                query_params = [
                    (key.strip(), value.strip())
                    for key, value in parse_qsl(params_value, keep_blank_values=True)
                    if key.strip()
                ]

                if query_params:
                    return HttpResponseRedirect(f"{base_url}?{urlencode(query_params)}")
                return HttpResponseRedirect(base_url)

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Reports"),
            "report_routes": report_routes,
            "selected_report": selected_report,
            "params_value": params_value,
        }
    )
    return TemplateResponse(request, "admin/system_reports.html", context)

def _system_details_view(request):
    """Render system details and privileged server restart actions."""

    info = _gather_info(_auto_upgrade_next_check)
    service_status = _suite_service_status()
    can_restart = (
        request.user.is_superuser
        and bool(service_status["configured"])
        and bool(service_status["is_active"])
    )

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("System"),
            "system_fields": _build_system_fields(info),
            "service_status": service_status,
            "can_restart_server": can_restart,
        }
    )
    return TemplateResponse(request, "admin/system_details.html", context)


def _system_restart_server_view(request):
    """Restart the configured suite systemd service for superusers."""

    if request.method != "POST":
        raise PermissionDenied
    if not request.user.is_superuser:
        raise PermissionDenied

    service_status = _suite_service_status()
    if not service_status["configured"] or not service_status["is_active"]:
        messages.error(
            request,
            _("Server restart is only available when the suite is active under systemd."),
        )
        return HttpResponseRedirect(reverse("admin:system-details"))

    command = _systemctl_command()
    if not command:
        messages.error(request, _("Systemd controls are unavailable on this node."))
        return HttpResponseRedirect(reverse("admin:system-details"))

    unit_name = str(service_status["unit_name"])
    try:
        subprocess.run(
            [*command, "restart", unit_name],
            check=True,
            cwd=Path(settings.BASE_DIR),
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        logger.exception("Unable to restart suite service %s", unit_name)
        messages.error(request, _("Failed to restart %(unit)s.") % {"unit": unit_name})
    else:
        messages.success(request, _("Restart requested for %(unit)s.") % {"unit": unit_name})

    return HttpResponseRedirect(reverse("admin:system-details"))


def _system_startup_report_view(request):
    try:
        limit = int(request.GET.get("limit", STARTUP_REPORT_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = STARTUP_REPORT_DEFAULT_LIMIT

    if limit < 1:
        limit = STARTUP_REPORT_DEFAULT_LIMIT

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Startup Report"),
            "startup_report": _read_startup_report(limit=limit),
            "startup_report_limit": limit,
            "startup_report_options": (10, 25, 50, 100, 200),
        }
    )
    return TemplateResponse(request, "admin/system_startup_report.html", context)


def _system_uptime_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Uptime Report"),
            "uptime_report": _build_uptime_report(),
        }
    )
    return TemplateResponse(request, "admin/system_uptime_report.html", context)


def _system_services_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Suite Services Report"),
            "services_report": _build_services_report(),
        }
    )
    return TemplateResponse(request, "admin/system_services_report.html", context)


def _system_nginx_report_view(request):
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("NGINX Report"),
            "nginx_report": _build_nginx_report(),
        }
    )
    return TemplateResponse(request, "admin/system_nginx_report.html", context)


def _system_upgrade_report_view(request):
    revision_info = None
    session = getattr(request, "session", None)
    if session is not None:
        revision_info = session.pop(UPGRADE_REVISION_SESSION_KEY, None)
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Upgrade Report"),
            "auto_upgrade_report": _build_auto_upgrade_report(
                **({"revision_info": revision_info} if revision_info is not None else {})
            ),
        }
    )
    return TemplateResponse(request, "admin/system_upgrade_report.html", context)


def _system_changelog_report_view(request):
    """Render the changelog report with lazy-loaded sections."""

    try:
        initial_page = changelog.get_initial_page()
    except changelog.ChangelogError as exc:
        initial_sections = tuple()
        has_more = False
        next_page = None
        error_message = str(exc)
    else:
        initial_sections = initial_page.sections
        has_more = initial_page.has_more
        next_page = initial_page.next_page
        error_message = ""

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Changelog Report"),
            "initial_sections": initial_sections,
            "has_more_sections": has_more,
            "next_page": next_page,
            "initial_section_count": len(initial_sections),
            "error_message": error_message,
            "loading_label": _("Loading more changes…"),
            "error_label": _("Unable to load additional changes."),
            "complete_label": _("You're all caught up."),
        }
    )
    return TemplateResponse(request, "admin/system_changelog_report.html", context)


def _system_changelog_report_data_view(request):
    """Return additional changelog sections for infinite scrolling."""

    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)

    try:
        offset = int(request.GET.get("offset", "0"))
    except ValueError:
        return JsonResponse({"error": _("Invalid offset.")}, status=400)

    try:
        page_data = changelog.get_page(page_number, per_page=1, offset=offset)
    except changelog.ChangelogError:
        logger.exception(
            "Failed to load changelog page %s (offset %s)", page_number, offset
        )
        return JsonResponse(
            {"error": _("Unable to load additional changes.")}, status=503
        )

    if not page_data.sections:
        return JsonResponse({"html": "", "has_more": False, "next_page": None})

    html = render_to_string(
        "includes/changelog/section_list.html",
        {"sections": page_data.sections, "variant": "admin"},
        request=request,
    )
    return JsonResponse(
        {"html": html, "has_more": page_data.has_more, "next_page": page_data.next_page}
    )


def _dismiss_admin_notice_view(request, notice_id: int):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:index"))

    notice = AdminNotice.objects.filter(pk=notice_id).first()
    if not notice:
        return HttpResponseRedirect(reverse("admin:index"))

    if not notice.dismissed_at:
        notice.dismissed_at = timezone.now()
        if getattr(request, "user", None) and request.user.is_authenticated:
            notice.dismissed_by = request.user
        notice.save(update_fields=["dismissed_at", "dismissed_by"])

    return HttpResponseRedirect(reverse("admin:index"))


def _system_trigger_upgrade_check_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    requested_channel = (request.POST.get("channel") or "stable").lower()
    channel_choice = UPGRADE_CHANNEL_CHOICES.get(
        requested_channel, UPGRADE_CHANNEL_CHOICES["stable"]
    )
    override_value = channel_choice.get("override")
    channel_override = override_value if isinstance(override_value, str) else None
    channel_label = None
    if requested_channel == "stable":
        channel_override = None
    elif channel_override:
        channel_label = str(channel_choice["label"])

    base_dir = Path(settings.BASE_DIR)
    _clear_auto_upgrade_skip_revisions(base_dir)

    try:
        queued = _trigger_upgrade_check(channel_override=channel_override)
    except Exception as exc:  # pragma: no cover - unexpected failure
        logger.exception("Unable to trigger upgrade check")
        messages.error(
            request,
            _("Unable to trigger an upgrade check: %(error)s")
            % {"error": str(exc)},
        )
    else:
        detail_message = ""
        if channel_label:
            detail_message = _(
                "It will run using the %(channel)s channel for this execution without changing the configured mode."
            ) % {"channel": channel_label}
        if queued:
            base_message = _("Upgrade check requested. The task will run shortly.")
        else:
            base_message = _(
                "Upgrade check started locally. Review the auto-upgrade log for progress."
            )
        if detail_message:
            messages.success(
                request,
                format_html("{} {}", base_message, detail_message),
            )
        else:
            messages.success(request, base_message)

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def _system_upgrade_revision_check_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    base_dir = Path(settings.BASE_DIR)
    revision_info = _load_upgrade_revision_info(base_dir)
    revision_info["revision_checked_at"] = timezone.now().isoformat()

    origin_revision = str(revision_info.get("origin_revision", ""))
    ci_status = ""
    if origin_revision:
        try:
            from apps.core.tasks.auto_upgrade import _ci_status_for_revision

            ci_status = _ci_status_for_revision(base_dir, origin_revision) or ""
        except Exception:  # pragma: no cover - unexpected failure path
            logger.exception("Unable to fetch CI status for revision %s", origin_revision)
            ci_status = ""

    revision_info["ci_status"] = ci_status

    if hasattr(request, "session"):
        request.session[UPGRADE_REVISION_SESSION_KEY] = revision_info

    messages.success(request, _("Pre-upgrade checks refreshed."))

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""

    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
            path(
                "system/details/",
                admin.site.admin_view(_system_details_view),
                name="system-details",
            ),
            path(
                "system/details/restart-server/",
                admin.site.admin_view(_system_restart_server_view),
                name="system-restart-server",
            ),
            path(
                "admin-notices/<int:notice_id>/dismiss/",
                admin.site.admin_view(_dismiss_admin_notice_view),
                name="dismiss-admin-notice",
            ),
            path(
                "system/reports/",
                admin.site.admin_view(_system_reports_view),
                name="system-reports",
            ),
            path(
                "system/startup-report/",
                admin.site.admin_view(_system_startup_report_view),
                name="system-startup-report",
            ),
            path(
                "system/changelog/",
                admin.site.admin_view(_system_changelog_report_view),
                name="system-changelog-report",
            ),
            path(
                "system/changelog/data/",
                admin.site.admin_view(_system_changelog_report_data_view),
                name="system-changelog-data",
            ),
            path(
                "system/uptime-report/",
                admin.site.admin_view(_system_uptime_report_view),
                name="system-uptime-report",
            ),
            path(
                "system/nginx-report/",
                admin.site.admin_view(_system_nginx_report_view),
                name="system-nginx-report",
            ),
            path(
                "system/services-report/",
                admin.site.admin_view(_system_services_report_view),
                name="system-services-report",
            ),
            path(
                "system/upgrade-report/",
                admin.site.admin_view(_system_upgrade_report_view),
                name="system-upgrade-report",
            ),
            path(
                "system/upgrade-report/check-revision/",
                admin.site.admin_view(_system_upgrade_revision_check_view),
                name="system-upgrade-check-revision",
            ),
            path(
                "system/upgrade-report/run-check/",
                admin.site.admin_view(_system_trigger_upgrade_check_view),
                name="system-upgrade-run-check",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
