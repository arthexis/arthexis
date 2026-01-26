from __future__ import annotations

from pathlib import Path
import logging

from django.conf import settings
from django.contrib import admin, messages
from django.http import HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.auto_upgrade import (
    auto_upgrade_base_dir,
    ensure_auto_upgrade_periodic_task,
    set_auto_upgrade_fast_lane,
)
from apps.core import changelog
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
    _load_upgrade_revision_info,
    _trigger_upgrade_check,
)


logger = logging.getLogger(__name__)


def _system_view(request):
    info = _gather_info()

    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("System"),
            "info": info,
            "system_fields": _build_system_fields(info),
        }
    )
    return TemplateResponse(request, "admin/system.html", context)


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
            from apps.core.tasks import _ci_status_for_revision

            ci_status = _ci_status_for_revision(base_dir, origin_revision) or ""
        except Exception:  # pragma: no cover - unexpected failure path
            logger.exception("Unable to fetch CI status for revision %s", origin_revision)
            ci_status = ""

    revision_info["ci_status"] = ci_status

    if hasattr(request, "session"):
        request.session[UPGRADE_REVISION_SESSION_KEY] = revision_info

    messages.success(request, _("Pre-upgrade checks refreshed."))

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def _system_toggle_fast_lane_view(request):
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    action = (request.POST.get("fast_lane_action") or "").strip().lower()
    enable = action == "enable"

    base_dir = auto_upgrade_base_dir()
    updated = set_auto_upgrade_fast_lane(enable, base_dir=base_dir)

    if updated:
        ensure_auto_upgrade_periodic_task(base_dir=base_dir)
        if enable:
            messages.success(
                request,
                _(
                    "Fast Lane enabled. Upgrade checks will run once per hour until disabled."
                ),
            )
        else:
            messages.success(
                request,
                _(
                    "Fast Lane disabled. Upgrade checks will run on the configured channel cadence."
                ),
            )
    else:
        messages.error(request, _("Unable to update Fast Lane mode."))

    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


def patch_admin_system_view() -> None:
    """Add custom admin view for system information."""

    original_get_urls = admin.site.get_urls

    def get_urls():
        urls = original_get_urls()
        custom = [
            path("system/", admin.site.admin_view(_system_view), name="system"),
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
            path(
                "system/upgrade-report/toggle-fast-lane/",
                admin.site.admin_view(_system_toggle_fast_lane_view),
                name="system-upgrade-toggle-fast-lane",
            ),
        ]
        return custom + urls

    admin.site.get_urls = get_urls
