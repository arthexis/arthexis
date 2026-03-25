import ipaddress
from urllib.parse import urlparse

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path
from django.urls import reverse
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.repos.forms import GitHubAppAdminForm
from apps.repos.admin_feedback_config import FeedbackIssueConfigurationAdminMixin
from apps.repos.models.events import GitHubEvent
from apps.repos.models.github_apps import GitHubApp, GitHubAppInstall
from apps.repos.models.github_tokens import GitHubToken
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository, PackageRepository


class FetchFromGitHubMixin(DjangoObjectActions):
    changelist_actions: list[str] = []
    dashboard_actions: list[str] = []

    def _redirect_to_changelist(self):
        opts = self.model._meta
        return HttpResponseRedirect(
            reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        )

    def get_dashboard_actions(self, request):
        return getattr(self, "dashboard_actions", [])


@admin.register(RepositoryIssue)
class RepositoryIssueAdmin(
    FeedbackIssueConfigurationAdminMixin, FetchFromGitHubMixin, admin.ModelAdmin
):
    actions = ["fetch_open_issues"]
    changelist_actions = ["fetch_open_issues"]
    change_actions = ("configure_action",)
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
class GitHubRepositoryAdmin(FetchFromGitHubMixin, admin.ModelAdmin):
    dashboard_actions = ["setup_token"]
    list_display = ("owner", "name", "is_private")
    search_fields = ("owner", "name")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "setup-token/",
                self.admin_site.admin_view(self.setup_token_view),
                name="repos_githubrepository_setup_token",
            )
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        response = super().changelist_view(request, extra_context=extra_context)
        content = getattr(response, "rendered_content", "")
        label = str(self.setup_token.label)
        if not content or label in content:
            return response
        link_markup = format_html(
            '<li><a href="{}" class="addlink">{}</a></li>',
            reverse("admin:repos_githubrepository_setup_token"),
            label,
        )
        response.content = content.replace(
            '<ul class="object-tools">',
            f'<ul class="object-tools">{link_markup}',
            1,
        )
        return response

    def setup_token_view(self, request):
        return self.setup_token(request)

    def setup_token(self, request, queryset=None):
        token = GitHubToken.objects.filter(user=request.user).order_by("pk").first()
        if token is not None:
            return HttpResponseRedirect(
                reverse("admin:repos_githubtoken_change", args=[token.pk])
            )
        return HttpResponseRedirect(reverse("admin:repos_githubtoken_add"))

    setup_token.label = _("Setup Token")
    setup_token.short_description = _("Setup Token")
    setup_token.requires_queryset = False
    setup_token.dashboard_url = "admin:repos_githubrepository_setup_token"


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

    def _warn_about_base_url(self, request) -> None:
        base_url = GitHubApp.instance_base_url()
        parsed = urlparse(base_url)
        host = parsed.hostname or ""
        needs_https = parsed.scheme != "https"
        if not host:
            needs_domain = True
        else:
            try:
                ipaddress.ip_address(host)
                needs_domain = True  # Host is an IP address.
            except ValueError:
                needs_domain = host.lower() == "localhost"

        if needs_https or needs_domain:
            self.message_user(
                request,
                _(
                    "GitHub Apps require a public HTTPS URL with a domain name. "
                    "Current base URL is %(base_url)s."
                )
                % {"base_url": base_url},
                level=messages.WARNING,
            )

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if request.method == "GET":
            self._warn_about_base_url(request)
        return super().changeform_view(
            request,
            object_id=object_id,
            form_url=form_url,
            extra_context=extra_context,
        )


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


@admin.register(GitHubToken)
class GitHubTokenAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("owner_display", "label")
    search_fields = ("label", "user__username", "group__name")
    raw_id_fields = ("user", "group")

    @admin.display(description=_("Owner"))
    def owner_display(self, obj):
        return obj.owner_display()
