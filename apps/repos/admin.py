from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

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

    fetch_open_issues.label = _("Fetch Open Issues")
    fetch_open_issues.short_description = _("Fetch Open Issues")
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

    fetch_open_pull_requests.label = _("Fetch Open Pull Requests")
    fetch_open_pull_requests.short_description = _("Fetch Open Pull Requests")
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

