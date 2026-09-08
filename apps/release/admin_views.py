"""Release-owned task-panel views formerly embedded in the core system admin."""

from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from apps.core.system import admin_views as core_admin_views
from apps.core.system.filesystem import _clear_auto_upgrade_skip_revisions
from apps.core.system.network import _upgrade_redirect
from apps.release import changelog
from apps.release.upgrade import (
    UPGRADE_CHANNEL_CHOICES,
    UPGRADE_REVISION_SESSION_KEY,
    _build_auto_upgrade_report,
    _load_upgrade_revision_info,
    _trigger_upgrade_check,
)

logger = logging.getLogger(__name__)
UPGRADE_CHECK_PERMISSION = "core.can_trigger_upgrade_checks"

_RELEASE_PANEL_ROUTE_NAMES = {
    "system-upgrade-report",
    "system-changelog-report",
    "system-changelog-data",
    "system-upgrade-run-check",
    "system-upgrade-check-revision",
}

# Remove the transitional core registrations before adding the owner versions.
core_admin_views.TASK_PANEL_ROUTES[:] = [
    route
    for route in core_admin_views.TASK_PANEL_ROUTES
    if route.name not in _RELEASE_PANEL_ROUTE_NAMES
]

task_panel_route = core_admin_views.task_panel_route


def _can_trigger_upgrade_checks(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_superuser or user.has_perm(UPGRADE_CHECK_PERMISSION))


@task_panel_route(
    route="system/upgrade-report/",
    name="system-upgrade-report",
    group="reports",
)
def _system_upgrade_report_view(request):
    if not _can_trigger_upgrade_checks(request.user):
        raise PermissionDenied

    revision_info = None
    session = getattr(request, "session", None)
    if session is not None:
        revision_info = session.pop(UPGRADE_REVISION_SESSION_KEY, None)
    context = admin.site.each_context(request)
    context.update(
        {
            "title": _("Upgrade Report"),
            "auto_upgrade_report": _build_auto_upgrade_report(
                **(
                    {"revision_info": revision_info}
                    if revision_info is not None
                    else {}
                )
            ),
        }
    )
    return TemplateResponse(request, "admin/system_upgrade_report.html", context)


@task_panel_route(
    route="system/changelog/",
    name="system-changelog-report",
    group="reports",
)
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


@task_panel_route(
    route="system/changelog/data/",
    name="system-changelog-data",
    group="reports",
)
def _system_changelog_report_data_view(request):
    """Return additional changelog sections for infinite scrolling."""

    try:
        page_number = int(request.GET.get("page", "1"))
    except ValueError:
        return JsonResponse({"error": _("Invalid page number.")}, status=400)
    if page_number < 1:
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
            {"error": _("Unable to load additional changes.")}, status=500
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


@task_panel_route(
    route="system/upgrade-report/run-check/",
    name="system-upgrade-run-check",
    group="reports",
)
def _system_trigger_upgrade_check_view(request):
    if not _can_trigger_upgrade_checks(request.user):
        raise PermissionDenied

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
            _("Unable to trigger an upgrade check: %(error)s") % {"error": str(exc)},
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


@task_panel_route(
    route="system/upgrade-report/check-revision/",
    name="system-upgrade-check-revision",
    group="reports",
)
def _system_upgrade_revision_check_view(request):
    if not _can_trigger_upgrade_checks(request.user):
        raise PermissionDenied

    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:system-upgrade-report"))

    base_dir = Path(settings.BASE_DIR)
    revision_info = _load_upgrade_revision_info(base_dir)
    revision_info["revision_checked_at"] = timezone.now().isoformat()

    origin_revision = str(revision_info.get("origin_revision", ""))
    ci_status = ""
    if origin_revision:
        try:
            from apps.release.tasks.auto_upgrade import _ci_status_for_revision

            ci_status = _ci_status_for_revision(base_dir, origin_revision) or ""
        except Exception:  # pragma: no cover - unexpected failure path
            logger.exception(
                "Unable to fetch CI status for revision %s", origin_revision
            )
            ci_status = ""

    revision_info["ci_status"] = ci_status

    if hasattr(request, "session"):
        request.session[UPGRADE_REVISION_SESSION_KEY] = revision_info

    messages.success(request, _("Pre-upgrade checks refreshed."))
    return _upgrade_redirect(request, reverse("admin:system-upgrade-report"))


# Legacy direct imports now resolve to the release-owned callables at runtime.
for _name in (
    "_system_upgrade_report_view",
    "_system_changelog_report_view",
    "_system_changelog_report_data_view",
    "_system_trigger_upgrade_check_view",
    "_system_upgrade_revision_check_view",
):
    setattr(core_admin_views, _name, globals()[_name])

del _name

__all__ = [
    "_system_upgrade_report_view",
    "_system_changelog_report_view",
    "_system_changelog_report_data_view",
    "_system_trigger_upgrade_check_view",
    "_system_upgrade_revision_check_view",
]
