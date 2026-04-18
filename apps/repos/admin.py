import ipaddress
from urllib.parse import urlparse

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django_object_actions import DjangoObjectActions

from apps.core.admin import OwnableAdminMixin
from apps.repos.admin_feedback_config import FeedbackIssueConfigurationAdminMixin
from apps.repos.forms import (
    GitHubAppAdminForm,
    GitHubCommentForm,
    GitHubConfirmForm,
    GitHubPullRequestMergeForm,
)
from apps.repos.models.events import GitHubEvent
from apps.repos.models.github_apps import GitHubApp, GitHubAppInstall
from apps.repos.models.github_tokens import GitHubToken
from apps.repos.models.issues import RepositoryIssue, RepositoryPullRequest
from apps.repos.models.repositories import GitHubRepository, PackageRepository
from apps.repos.release_management import (
    ReleaseManagementClient,
    ReleaseManagementError,
    RepositoryRef,
)


class FetchFromGitHubMixin(DjangoObjectActions):
    changelist_actions: list[str] = []
    dashboard_actions: list[str] = []
    operation_template_name = "admin/repos/github_operation.html"
    observation_template_name = "admin/repos/github_observation.html"

    def _redirect_to_changelist(self):
        opts = self.model._meta
        return HttpResponseRedirect(
            reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
        )

    def _change_url(self, obj):
        opts = self.model._meta
        return reverse(f"admin:{opts.app_label}_{opts.model_name}_change", args=[obj.pk])

    def _get_change_object(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404(_("Object not found."))
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        return obj

    def _render_operation_view(
        self,
        request,
        *,
        obj,
        form,
        title,
        impact_note,
        submit_label,
    ):
        opts = self.model._meta
        context = {
            **self.admin_site.each_context(request),
            "action_name": submit_label,
            "change_url": self._change_url(obj),
            "form": form,
            "impact_note": impact_note,
            "opts": opts,
            "original": obj,
            "target_label": str(obj),
            "title": title,
        }
        return TemplateResponse(request, self.operation_template_name, context)

    def _render_observation_view(
        self,
        request,
        *,
        obj,
        title,
        impact_note,
        activity,
        action_links,
        summary_rows,
    ):
        opts = self.model._meta
        context = {
            **self.admin_site.each_context(request),
            "action_links": action_links,
            "activity": activity,
            "change_url": self._change_url(obj),
            "impact_note": impact_note,
            "object_url": getattr(obj, "html_url", ""),
            "opts": opts,
            "original": obj,
            "summary_rows": summary_rows,
            "target_label": str(obj),
            "title": title,
        }
        return TemplateResponse(request, self.observation_template_name, context)

    @staticmethod
    def _repository_ref(obj) -> RepositoryRef:
        return RepositoryRef(owner=obj.repository.owner, name=obj.repository.name)

    def get_dashboard_actions(self, request):
        return getattr(self, "dashboard_actions", [])


@admin.register(RepositoryIssue)
class RepositoryIssueAdmin(
    FeedbackIssueConfigurationAdminMixin, FetchFromGitHubMixin, admin.ModelAdmin
):
    actions = ["fetch_open_issues"]
    changelist_actions = ["fetch_open_issues"]
    change_actions = ("configure_action", "observe_action", "comment_action", "close_action")
    list_display = (
        "number_link",
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

    @admin.display(description=_("Issue"), ordering="number")
    def number_link(self, obj):
        if obj.html_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">#{}</a>',
                obj.html_url,
                obj.number,
            )
        return f"#{obj.number}"

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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/observe/",
                self.admin_site.admin_view(self.observe_view),
                name="repos_repositoryissue_observe",
            ),
            path(
                "<path:object_id>/comment/",
                self.admin_site.admin_view(self.comment_view),
                name="repos_repositoryissue_comment",
            ),
            path(
                "<path:object_id>/close/",
                self.admin_site.admin_view(self.close_view),
                name="repos_repositoryissue_close",
            ),
        ]
        return custom_urls + urls

    def get_change_actions(self, request, object_id, form_url):
        actions = list(super().get_change_actions(request, object_id, form_url))
        obj = self.get_object(request, object_id)
        if obj is not None and obj.state != "open" and "close_action" in actions:
            actions.remove("close_action")
        return actions

    def comment_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositoryissue_comment", args=[obj.pk])
        )

    def observe_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositoryissue_observe", args=[obj.pk])
        )

    observe_action.label = _("Observe")
    observe_action.short_description = _("Observe")
    observe_action.requires_queryset = False

    comment_action.label = _("Comment")
    comment_action.short_description = _("Comment")
    comment_action.requires_queryset = False

    def close_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositoryissue_close", args=[obj.pk])
        )

    close_action.label = _("Close")
    close_action.short_description = _("Close")
    close_action.requires_queryset = False

    def observe_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        try:
            activity = ReleaseManagementClient().list_issue_activity(
                self._repository_ref(obj),
                number=obj.number,
            )
        except ReleaseManagementError as exc:
            self.message_user(
                request,
                _("Failed to load GitHub issue activity: %s") % (exc,),
                level=messages.ERROR,
            )
            activity = []

        return self._render_observation_view(
            request,
            obj=obj,
            title=_("Observe GitHub issue"),
            impact_note=_(
                "This view shows live GitHub comments and reviewer reaction icons."
            ),
            activity=activity,
            action_links=[
                {
                    "label": _("Comment"),
                    "url": reverse("admin:repos_repositoryissue_comment", args=[obj.pk]),
                },
                {
                    "label": _("Close"),
                    "url": reverse("admin:repos_repositoryissue_close", args=[obj.pk]),
                }
                if obj.state == "open"
                else {},
            ],
            summary_rows=[
                (_("State"), obj.state),
                (_("Author"), obj.author or _("Unknown")),
                (_("Updated"), obj.updated_at),
            ],
        )

    def comment_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        form = GitHubCommentForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                ReleaseManagementClient().comment_issue(
                    self._repository_ref(obj),
                    number=obj.number,
                    body=form.cleaned_data["body"],
                )
            except ReleaseManagementError as exc:
                self.message_user(
                    request,
                    _("Failed to add GitHub issue comment: %s") % (exc,),
                    level=messages.ERROR,
                )
            else:
                obj.updated_at = timezone.now()
                obj.save(update_fields=["updated_at"])
                self.message_user(
                    request,
                    _("Comment added to GitHub issue #%(number)s.") % {
                        "number": obj.number,
                    },
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._change_url(obj))
        return self._render_operation_view(
            request,
            obj=obj,
            form=form,
            title=_("Comment on GitHub issue"),
            impact_note=_("This posts a public comment to the linked GitHub issue."),
            submit_label=_("Post comment"),
        )

    def close_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        form = GitHubConfirmForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                ReleaseManagementClient().close_issue(
                    self._repository_ref(obj),
                    number=obj.number,
                )
            except ReleaseManagementError as exc:
                self.message_user(
                    request,
                    _("Failed to close GitHub issue: %s") % (exc,),
                    level=messages.ERROR,
                )
            else:
                obj.state = "closed"
                obj.updated_at = timezone.now()
                obj.save(update_fields=["state", "updated_at"])
                self.message_user(
                    request,
                    _("GitHub issue #%(number)s closed.") % {"number": obj.number},
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._change_url(obj))
        return self._render_operation_view(
            request,
            obj=obj,
            form=form,
            title=_("Close GitHub issue"),
            impact_note=_("This will close the linked GitHub issue."),
            submit_label=_("Close issue"),
        )


@admin.register(RepositoryPullRequest)
class RepositoryPullRequestAdmin(FetchFromGitHubMixin, admin.ModelAdmin):
    actions = ["fetch_open_pull_requests"]
    changelist_actions = ["fetch_open_pull_requests"]
    change_actions = ("observe_action", "comment_action", "ready_action", "merge_action")
    list_display = (
        "number_link",
        "title",
        "repository",
        "state",
        "is_draft",
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

    @admin.display(description=_("Pull request"), ordering="number")
    def number_link(self, obj):
        if obj.html_url:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">PR #{}</a>',
                obj.html_url,
                obj.number,
            )
        return f"PR #{obj.number}"

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

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/observe/",
                self.admin_site.admin_view(self.observe_view),
                name="repos_repositorypullrequest_observe",
            ),
            path(
                "<path:object_id>/comment/",
                self.admin_site.admin_view(self.comment_view),
                name="repos_repositorypullrequest_comment",
            ),
            path(
                "<path:object_id>/ready/",
                self.admin_site.admin_view(self.ready_view),
                name="repos_repositorypullrequest_ready",
            ),
            path(
                "<path:object_id>/merge/",
                self.admin_site.admin_view(self.merge_view),
                name="repos_repositorypullrequest_merge",
            ),
        ]
        return custom_urls + urls

    def get_change_actions(self, request, object_id, form_url):
        actions = list(super().get_change_actions(request, object_id, form_url))
        obj = self.get_object(request, object_id)
        if obj is None:
            return actions
        if obj.state != "open":
            for action_name in ("comment_action", "ready_action", "merge_action"):
                if action_name in actions:
                    actions.remove(action_name)
        elif not obj.is_draft and "ready_action" in actions:
            actions.remove("ready_action")
        return actions

    def observe_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositorypullrequest_observe", args=[obj.pk])
        )

    observe_action.label = _("Observe")
    observe_action.short_description = _("Observe")
    observe_action.requires_queryset = False

    def comment_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositorypullrequest_comment", args=[obj.pk])
        )

    comment_action.label = _("Comment")
    comment_action.short_description = _("Comment")
    comment_action.requires_queryset = False

    def ready_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositorypullrequest_ready", args=[obj.pk])
        )

    ready_action.label = _("Ready")
    ready_action.short_description = _("Ready")
    ready_action.requires_queryset = False

    def merge_action(self, request, obj):
        return HttpResponseRedirect(
            reverse("admin:repos_repositorypullrequest_merge", args=[obj.pk])
        )

    merge_action.label = _("Merge")
    merge_action.short_description = _("Merge")
    merge_action.requires_queryset = False

    def observe_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        try:
            activity = ReleaseManagementClient().list_pull_request_activity(
                self._repository_ref(obj),
                number=obj.number,
            )
        except ReleaseManagementError as exc:
            self.message_user(
                request,
                _("Failed to load pull request activity: %s") % (exc,),
                level=messages.ERROR,
            )
            activity = []

        action_links = []
        if obj.state == "open":
            action_links.append(
                {
                    "label": _("Comment"),
                    "url": reverse(
                        "admin:repos_repositorypullrequest_comment", args=[obj.pk]
                    ),
                }
            )
        if obj.state == "open" and obj.is_draft:
            action_links.append(
                {
                    "label": _("Ready"),
                    "url": reverse(
                        "admin:repos_repositorypullrequest_ready", args=[obj.pk]
                    ),
                }
            )
        if obj.state == "open":
            action_links.append(
                {
                    "label": _("Merge"),
                    "url": reverse(
                        "admin:repos_repositorypullrequest_merge", args=[obj.pk]
                    ),
                }
            )

        return self._render_observation_view(
            request,
            obj=obj,
            title=_("Observe pull request"),
            impact_note=_(
                "This view shows live GitHub comments, review comments, and reviewer reaction icons."
            ),
            activity=activity,
            action_links=action_links,
            summary_rows=[
                (_("State"), obj.state),
                (_("Draft"), _("Yes") if obj.is_draft else _("No")),
                (_("Source branch"), obj.source_branch or _("Unknown")),
                (_("Target branch"), obj.target_branch or _("Unknown")),
                (_("Updated"), obj.updated_at),
            ],
        )

    def comment_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        form = GitHubCommentForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                ReleaseManagementClient().comment_pull_request(
                    self._repository_ref(obj),
                    number=obj.number,
                    body=form.cleaned_data["body"],
                )
            except ReleaseManagementError as exc:
                self.message_user(
                    request,
                    _("Failed to add pull request comment: %s") % (exc,),
                    level=messages.ERROR,
                )
            else:
                obj.updated_at = timezone.now()
                obj.save(update_fields=["updated_at"])
                self.message_user(
                    request,
                    _("Comment added to pull request #%(number)s.") % {
                        "number": obj.number,
                    },
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._change_url(obj))
        return self._render_operation_view(
            request,
            obj=obj,
            form=form,
            title=_("Comment on pull request"),
            impact_note=_("This posts a public comment to the linked GitHub pull request."),
            submit_label=_("Post comment"),
        )

    def ready_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        form = GitHubConfirmForm(request.POST if request.method == "POST" else None)
        if request.method == "POST" and form.is_valid():
            try:
                ReleaseManagementClient().mark_pull_request_ready(
                    self._repository_ref(obj),
                    number=obj.number,
                )
            except ReleaseManagementError as exc:
                self.message_user(
                    request,
                    _("Failed to move pull request out of draft: %s") % (exc,),
                    level=messages.ERROR,
                )
            else:
                obj.is_draft = False
                obj.updated_at = timezone.now()
                obj.save(update_fields=["is_draft", "updated_at"])
                self.message_user(
                    request,
                    _("Pull request #%(number)s is ready for review.") % {
                        "number": obj.number,
                    },
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._change_url(obj))
        return self._render_operation_view(
            request,
            obj=obj,
            form=form,
            title=_("Move pull request out of draft"),
            impact_note=_("This marks the linked GitHub pull request as ready for review."),
            submit_label=_("Ready for review"),
        )

    def merge_view(self, request, object_id):
        obj = self._get_change_object(request, object_id)
        form = GitHubPullRequestMergeForm(
            request.POST if request.method == "POST" else None
        )
        if request.method == "POST" and form.is_valid():
            merge_method = form.cleaned_data["merge_method"]
            try:
                ReleaseManagementClient().merge_pull_request(
                    self._repository_ref(obj),
                    number=obj.number,
                    merge_method=merge_method,
                )
            except ReleaseManagementError as exc:
                self.message_user(
                    request,
                    _("Failed to merge pull request: %s") % (exc,),
                    level=messages.ERROR,
                )
            else:
                merged_at = timezone.now()
                obj.is_draft = False
                obj.merged_at = merged_at
                obj.state = "closed"
                obj.updated_at = merged_at
                obj.save(
                    update_fields=["is_draft", "merged_at", "state", "updated_at"]
                )
                self.message_user(
                    request,
                    _(
                        "Pull request #%(number)s merged with %(method)s."
                    )
                    % {"number": obj.number, "method": merge_method},
                    level=messages.SUCCESS,
                )
                return HttpResponseRedirect(self._change_url(obj))
        return self._render_operation_view(
            request,
            obj=obj,
            form=form,
            title=_("Merge pull request"),
            impact_note=_("This merges the linked GitHub pull request using the selected method."),
            submit_label=_("Merge pull request"),
        )


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
