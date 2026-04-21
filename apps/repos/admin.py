import ipaddress
from urllib.parse import urlparse

from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.repos.admin_feedback_config import FeedbackIssueConfigurationAdminMixin
from apps.repos.forms import GitHubAppAdminForm
from apps.repos.models.events import GitHubEvent
from apps.repos.models.github_apps import GitHubApp, GitHubAppInstall
from apps.repos.models.github_tokens import GitHubToken
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository, PackageRepository
from apps.repos.models.response_templates import GitHubResponseTemplate
from apps.repos.models.spam import RepositoryIssueSpamAssessment


class FetchFromGitHubMixin(DjangoObjectActions):
    changelist_actions: list[str] = []
    dashboard_actions: list[str] = []

    def _run_fetch_from_github_action(
        self,
        request,
        *,
        sync_function,
        error_message_template,
        success_message_template,
        empty_state_message_template,
    ):
        try:
            created, updated = sync_function()
        except Exception as exc:  # pragma: no cover - defensive
            self.message_user(
                request,
                error_message_template % {"error": exc},
                level=messages.ERROR,
            )
            return self._redirect_to_changelist()

        if created or updated:
            message = success_message_template % {
                "created": created,
                "updated": updated,
            }
            level = messages.SUCCESS
        else:
            message = empty_state_message_template
            level = messages.INFO

        self.message_user(request, message, level=level)
        return self._redirect_to_changelist()

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
        return self._run_fetch_from_github_action(
            request,
            sync_function=RepositoryIssue.fetch_open_issues,
            error_message_template=_("Failed to fetch issues from GitHub: %(error)s"),
            success_message_template=_(
                "Fetched %(created)s new and %(updated)s updated issues."
            ),
            empty_state_message_template=_("No open issues found to sync."),
        )

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
        return self._run_fetch_from_github_action(
            request,
            sync_function=RepositoryPullRequest.fetch_open_pull_requests,
            error_message_template=_(
                "Failed to fetch pull requests from GitHub: %(error)s"
            ),
            success_message_template=_(
                "Fetched %(created)s new and %(updated)s updated pull requests."
            ),
            empty_state_message_template=_("No open pull requests found to sync."),
        )

    fetch_open_pull_requests.label = _("Fetch Open")
    fetch_open_pull_requests.short_description = _("Fetch Open")
    fetch_open_pull_requests.requires_queryset = False


@admin.register(GitHubRepository)
class GitHubRepositoryAdmin(FetchFromGitHubMixin, admin.ModelAdmin):
    dashboard_actions = ["setup_token"]
    list_display = ("owner", "name", "is_private")
    search_fields = ("owner", "name")

    def _github_token_admin(self):
        return self.admin_site._registry.get(GitHubToken)

    def _can_setup_token(self, request, token=None):
        token_admin = self._github_token_admin()
        if token_admin is None:
            return False
        if token is None:
            return token_admin.has_add_permission(request)
        return token_admin.has_change_permission(request, obj=token)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "setup-token/",
                self.admin_site.admin_view(self.setup_token),
                name="repos_githubrepository_setup_token",
            )
        ]
        return custom_urls + urls

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        links = list(extra_context.get("public_view_links") or [])
        setup_url = reverse("admin:repos_githubrepository_setup_token")
        if not any(link.get("url") == setup_url for link in links):
            links.append({"label": self.setup_token.label, "url": setup_url})
        extra_context["public_view_links"] = links
        return super().changelist_view(request, extra_context=extra_context)

    def get_dashboard_actions(self, request):
        if not self._can_setup_token(request):
            return []
        return super().get_dashboard_actions(request)

    def setup_token(self, request, queryset=None):
        token = GitHubToken.objects.filter(user=request.user).order_by("pk").first()
        if token is not None:
            if not self._can_setup_token(request, token=token):
                self.message_user(
                    request,
                    _("You do not have permission to change your GitHub token."),
                    level=messages.WARNING,
                )
                return self._redirect_to_changelist()
            return HttpResponseRedirect(
                reverse("admin:repos_githubtoken_change", args=[token.pk])
            )
        if not self._can_setup_token(request):
            self.message_user(
                request,
                _("You do not have permission to add a GitHub token."),
                level=messages.WARNING,
            )
            return self._redirect_to_changelist()
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


@admin.register(GitHubResponseTemplate)
class GitHubResponseTemplateAdmin(OwnableAdminMixin, admin.ModelAdmin):
    list_display = ("label", "user", "is_active")
    list_filter = ("is_active",)
    search_fields = ("label", "body", "user__username")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(user=request.user)

    def save_model(self, request, obj, form, change):
        obj.user = request.user
        super().save_model(request, obj, form, change)


@admin.register(RepositoryIssueSpamAssessment)
class RepositoryIssueSpamAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "issue_number",
        "repository",
        "issue_author",
        "action",
        "is_spam",
        "score",
        "processed_at",
    )
    list_filter = ("is_spam", "action", "processed_at", "repository")
    search_fields = (
        "=issue_number",
        "issue_title",
        "issue_author",
        "delivery_id",
        "repository__owner",
        "repository__name",
    )
    readonly_fields = (
        "processed_at",
        "delivery_id",
        "issue_number",
        "issue_title",
        "issue_body",
        "issue_author",
        "action",
        "is_spam",
        "score",
        "reasons",
        "event",
    )
    raw_id_fields = ("event", "repository")


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
