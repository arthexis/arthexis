import logging

from django.apps import apps as django_apps
from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from django_object_actions import DjangoObjectActions

from apps.locals.user_data import EntityModelAdmin

REPOS_APP_INSTALLED = django_apps.is_installed("apps.repos")

if REPOS_APP_INSTALLED:
    from apps.repos.admin_feedback_config import FeedbackIssueConfigurationAdminMixin
    from apps.repos.permissions import REPOSITORY_WORK_VIEW_PERMISSIONS
else:
    REPOSITORY_WORK_VIEW_PERMISSIONS = ()

    class FeedbackIssueConfigurationAdminMixin:
        """No-op mixin used when Repos admin tooling is unavailable."""

from ..models import UserStory, UserStoryAttachment

logger = logging.getLogger(__name__)


class UserStoryAttachmentInline(admin.TabularInline):
    """Read-only attachment list for user story records."""

    model = UserStoryAttachment
    extra = 0
    fields = ("file", "uploaded_at")
    readonly_fields = ("file", "uploaded_at")
    can_delete = False


@admin.register(UserStory)
class UserStoryAdmin(
    FeedbackIssueConfigurationAdminMixin, DjangoObjectActions, EntityModelAdmin
):
    date_hierarchy = "submitted_at"
    actions = ["create_github_issues", "mark_selected_as_spam"]
    change_actions = ("configure_action",) if REPOS_APP_INSTALLED else ()
    dashboard_actions = ("repository_work_dashboard",) if REPOS_APP_INSTALLED else ()
    list_display = (
        "name",
        "language_code",
        "rating",
        "path",
        "issue_destination",
        "status",
        "submitted_at",
        "github_issue_display",
        "owner",
        "javascript_enabled",
        "assign_to",
    )
    list_filter = ("rating", "issue_destination", "status", "submitted_at")
    search_fields = (
        "name",
        "comments",
        "path",
        "language_code",
        "referer",
        "github_issue_url",
        "ip_address",
        "issue_destination",
    )
    readonly_fields = (
        "name",
        "rating",
        "comments",
        "legacy_screenshot",
        "path",
        "user",
        "owner",
        "javascript_enabled",
        "language_code",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "issue_destination",
        "github_issue_number",
        "github_issue_url",
        "allow_feedback_issue_label_tags",
        "feedback_tags_display",
    )
    ordering = ("-submitted_at",)
    inlines = (UserStoryAttachmentInline,)
    fields = (
        "name",
        "rating",
        "comments",
        "legacy_screenshot",
        "path",
        "language_code",
        "user",
        "owner",
        "javascript_enabled",
        "status",
        "assign_to",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "issue_destination",
        "allow_feedback_issue_label_tags",
        "feedback_tags_display",
        "github_issue_number",
        "github_issue_url",
    )

    def get_dashboard_actions(self, request):
        if not REPOS_APP_INSTALLED:
            return ()
        if request.user.has_perms(REPOSITORY_WORK_VIEW_PERMISSIONS):
            return self.dashboard_actions
        return ()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not REPOS_APP_INSTALLED:
            actions.pop("create_github_issues", None)
        return actions

    def repository_work_dashboard(self, request, queryset=None):
        return HttpResponseRedirect(reverse("repos:repository-work-dashboard"))

    repository_work_dashboard.label = _("Issues")
    repository_work_dashboard.short_description = _("Issues")
    repository_work_dashboard.requires_queryset = False
    repository_work_dashboard.dashboard_url = "repos:repository-work-dashboard"

    @admin.display(description=_("GitHub issue"), ordering="github_issue_number")
    def github_issue_display(self, obj):
        if obj.github_issue_url:
            label = (
                f"#{obj.github_issue_number}"
                if obj.github_issue_number is not None
                else obj.github_issue_url
            )
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
                obj.github_issue_url,
                label,
            )
        if obj.github_issue_number is not None:
            return f"#{obj.github_issue_number}"
        return ""

    @admin.display(description=_("Screenshot"))
    def legacy_screenshot(self, obj):
        if not obj.screenshot:
            return ""

        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.screenshot.url,
            obj.screenshot.name,
        )

    @admin.display(description=_("Feedback tags"))
    def feedback_tags_display(self, obj):
        return ", ".join(f"#{tag}" for tag in obj.feedback_tags or [])

    @admin.action(description=_("Create GitHub issues"))
    def create_github_issues(self, request, queryset):
        created = 0
        skipped = 0

        for story in queryset:
            if story.github_issue_url or story.is_local_issue:
                skipped += 1
                continue

            try:
                issue_url = story.create_github_issue()
            except Exception as exc:  # pragma: no cover - network/runtime errors
                logger.exception(
                    "Failed to create GitHub issue for UserStory %s", story.pk
                )
                message = _(
                    "Unable to create a GitHub issue for %(story)s: %(error)s"
                ) % {
                    "story": story,
                    "error": exc,
                }

                if isinstance(
                    exc, RuntimeError
                ) and "GitHub token is not configured" in str(exc):
                    message = format_html(
                        "{} {}",
                        message,
                        _("Set the GITHUB_TOKEN or GH_TOKEN environment variable."),
                    )

                self.message_user(
                    request,
                    message,
                    messages.ERROR,
                )
                continue

            if issue_url:
                created += 1
            else:
                skipped += 1

        if created:
            self.message_user(
                request,
                ngettext(
                    "Created %(count)d GitHub issue.",
                    "Created %(count)d GitHub issues.",
                    created,
                )
                % {"count": created},
                messages.SUCCESS,
            )

        if skipped:
            self.message_user(
                request,
                ngettext(
                    "Skipped %(count)d feedback item (already linked, local, or throttled).",
                    "Skipped %(count)d feedback items (already linked, local, or throttled).",
                    skipped,
                )
                % {"count": skipped},
                messages.INFO,
            )

    @admin.action(description=_("Mark selected as spam"))
    def mark_selected_as_spam(self, request, queryset):
        updated = 0
        for story in queryset.exclude(status=UserStory.Status.SPAM).iterator():
            story.status = UserStory.Status.SPAM
            story.save(update_fields=["status"])
            updated += 1
        if updated:
            self.message_user(
                request,
                ngettext(
                    "Marked %(count)d feedback item as spam.",
                    "Marked %(count)d feedback items as spam.",
                    updated,
                )
                % {"count": updated},
                messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                _("Selected feedback items are already marked as spam."),
                messages.INFO,
            )

    def has_add_permission(self, request):
        return False
