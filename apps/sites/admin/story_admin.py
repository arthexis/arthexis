import logging

from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _, ngettext

from apps.locals.user_data import EntityModelAdmin

from ..models import UserStory


logger = logging.getLogger(__name__)


@admin.register(UserStory)
class UserStoryAdmin(EntityModelAdmin):
    date_hierarchy = "submitted_at"
    actions = ["create_github_issues", "mark_selected_as_spam"]
    list_display = (
        "name",
        "language_code",
        "rating",
        "path",
        "status",
        "submitted_at",
        "github_issue_display",
        "owner",
        "assign_to",
    )
    list_filter = ("rating", "status", "submitted_at")
    search_fields = (
        "name",
        "comments",
        "path",
        "language_code",
        "referer",
        "github_issue_url",
        "ip_address",
    )
    readonly_fields = (
        "name",
        "rating",
        "comments",
        "path",
        "user",
        "owner",
        "language_code",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "github_issue_number",
        "github_issue_url",
    )
    ordering = ("-submitted_at",)
    fields = (
        "name",
        "rating",
        "comments",
        "path",
        "language_code",
        "user",
        "owner",
        "status",
        "assign_to",
        "referer",
        "user_agent",
        "ip_address",
        "created_on",
        "submitted_at",
        "github_issue_number",
        "github_issue_url",
    )

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

    @admin.action(description=_("Create GitHub issues"))
    def create_github_issues(self, request, queryset):
        created = 0
        skipped = 0

        for story in queryset:
            if story.github_issue_url:
                skipped += 1
                continue

            try:
                issue_url = story.create_github_issue()
            except Exception as exc:  # pragma: no cover - network/runtime errors
                logger.exception("Failed to create GitHub issue for UserStory %s", story.pk)
                message = _("Unable to create a GitHub issue for %(story)s: %(error)s") % {
                    "story": story,
                    "error": exc,
                }

                if (
                    isinstance(exc, RuntimeError)
                    and "GitHub token is not configured" in str(exc)
                ):
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
                    "Skipped %(count)d feedback item (issue already exists or was throttled).",
                    "Skipped %(count)d feedback items (issues already exists or was throttled).",
                    skipped,
                )
                % {"count": skipped},
                messages.INFO,
            )

    @admin.action(description=_("Mark selected as spam"))
    def mark_selected_as_spam(self, request, queryset):
        updated = queryset.exclude(status=UserStory.Status.SPAM).update(
            status=UserStory.Status.SPAM
        )
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
