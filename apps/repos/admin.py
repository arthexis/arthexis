from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.repos.forms import GitHubAppAdminForm
from apps.repos.models.events import GitHubEvent
from apps.repos.models.github_apps import GitHubApp, GitHubAppInstall
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository, PackageRepository


class FetchFromGitHubMixin(DjangoObjectActions):
    changelist_actions: list[str] = []

    def _redirect_to_changelist(self):
        opts = self.model._meta
        return HttpResponseRedirect(
            reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        )


@admin.register(RepositoryIssue)
class RepositoryIssueAdmin(FetchFromGitHubMixin, admin.ModelAdmin):
    actions = ["fetch_open_issues"]
    changelist_actions = ["fetch_open_issues"]
    list_display = (
        "number",
        "title",
        "repository",
        "state",
        "author",
        "updated_at",
    )
    list_filter = ("state", "repository")
    search_fields = (
        "title",
        "number",
        "repository__owner",
        "repository__name",
    )
    raw_id_fields = ("repository",)

    def fetch_open_issues(self, request, queryset=None):
        try:
            created, updated = RepositoryIssue.fetch_open_issues()
        except Exception as exc:  # pragma: no cover - defensive
            self.message_user(
                request,
                _("Failed to fetch issues from GitHub: %s") % (exc,),
                level=messages.ERROR,
            )
            return self._redirect_to_changelist()

        if created or updated:
            message = _("Fetched %(created)s new and %(updated)s updated issues.") % {
                "created": created,
                "updated": updated,
            }
            level = messages.SUCCESS
        else:
            message = _("No open issues found to sync.")
            level = messages.INFO

        self.message_user(request, message, level=level)
        return self._redirect_to_changelist()

    fetch_open_issues.label = _("Fetch Open")
    fetch_open_issues.short_description = _("Fetch Open")
    fetch_open_issues.requires_queryset = False


@admin.register(RepositoryPullRequest)
class RepositoryPullRequestAdmin(FetchFromGitHubMixin, admin.ModelAdmin):
    actions = ["fetch_open_pull_requests"]
    changelist_actions = ["fetch_open_pull_requests"]
    list_display = (
        "number",
        "title",
        "repository",
        "state",
        "author",
        "updated_at",
    )
    list_filter = ("state", "is_draft", "repository")
    search_fields = (
        "title",
        "number",
        "repository__owner",
        "repository__name",
    )
    raw_id_fields = ("repository",)

    def fetch_open_pull_requests(self, request, queryset=None):
        try:
            created, updated = RepositoryPullRequest.fetch_open_pull_requests()
        except Exception as exc:  # pragma: no cover - defensive
            self.message_user(
                request,
                _("Failed to fetch pull requests from GitHub: %s") % (exc,),
                level=messages.ERROR,
            )
            return self._redirect_to_changelist()

        if created or updated:
            message = _(
                "Fetched %(created)s new and %(updated)s updated pull requests."
            ) % {
                "created": created,
                "updated": updated,
            }
            level = messages.SUCCESS
        else:
            message = _("No open pull requests found to sync.")
            level = messages.INFO

        self.message_user(request, message, level=level)
        return self._redirect_to_changelist()

    fetch_open_pull_requests.label = _("Fetch Open")
    fetch_open_pull_requests.short_description = _("Fetch Open")
    fetch_open_pull_requests.requires_queryset = False


@admin.register(GitHubRepository)
class GitHubRepositoryAdmin(admin.ModelAdmin):
    list_display = ("owner", "name", "is_private")
    search_fields = ("owner", "name")


@admin.register(PackageRepository)
class PackageRepositoryAdmin(admin.ModelAdmin):
    list_display = ("name", "repository_url", "verify_availability")
    search_fields = ("name", "repository_url")
    filter_horizontal = ("packages",)


@admin.register(GitHubEvent)
class GitHubEventAdmin(admin.ModelAdmin):
    list_display = (
        "event_type",
        "delivery_id",
        "repository",
        "owner",
        "name",
        "received_at",
    )
    list_filter = ("event_type", "received_at")
    search_fields = ("delivery_id", "event_type", "owner", "name")
    readonly_fields = (
        "received_at",
        "http_method",
        "headers",
        "query_params",
        "payload",
        "raw_body",
        "event_type",
        "delivery_id",
        "hook_id",
        "signature",
        "signature_256",
        "user_agent",
    )
    raw_id_fields = ("repository",)


@admin.register(GitHubApp)
class GitHubAppAdmin(admin.ModelAdmin):
    form = GitHubAppAdminForm
    list_display = ("display_name", "app_id", "app_slug", "auth_method")
    search_fields = ("display_name", "app_slug", "=app_id")
    list_filter = ("auth_method",)
    raw_id_fields = ("auth_user",)


@admin.register(GitHubAppInstall)
class GitHubAppInstallAdmin(admin.ModelAdmin):
    list_display = (
        "app",
        "installation_id",
        "account_login",
        "target_type",
        "repository_selection",
        "installed_at",
        "suspended_at",
    )
    list_filter = ("target_type", "repository_selection")
    search_fields = ("=installation_id", "account_login")
    raw_id_fields = ("app",)
