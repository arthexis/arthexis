"""Issue and pull request models."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.repos import github
from apps.repos.models.repositories import GitHubRepository
from apps.repos.services import github as github_service


class RepositoryIssue(Entity):
    """A stored reference to a GitHub issue for a repository."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="issues",
        on_delete=models.CASCADE,
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=500)
    state = models.CharField(max_length=50)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    labels = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "number"],
                name="unique_issue_per_repository",
            )
        ]
        verbose_name = _("Repository Issue")
        verbose_name_plural = _("Repository Issues")

    def __str__(self):  # pragma: no cover - simple representation
        return f"#{self.number} {self.title}".strip()

    @classmethod
    def fetch_open_issues(
        cls, repository: GitHubRepository | None = None, token: str | None = None
    ) -> tuple[int, int]:
        repository = repository or GitHubRepository.resolve_active_repository()
        token = token or github_service.get_github_issue_token()
        repo_obj = github.ensure_repository(repository)

        created = 0
        updated = 0

        for item in github_service.fetch_repository_issues(
            token=token, owner=repo_obj.owner, name=repo_obj.name
        ):
            if "pull_request" in item:
                continue

            number = item.get("number")
            if not isinstance(number, int):
                continue

            defaults = {
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "html_url": item.get("html_url") or "",
                "api_url": item.get("url") or "",
                "author": (item.get("user") or {}).get("login") or "",
                "labels": github.extract_label_names(item),
                "created_at": github.parse_github_timestamp(item.get("created_at")),
                "updated_at": github.parse_github_timestamp(item.get("updated_at")),
            }

            _, was_created = cls.objects.update_or_create(
                repository=repo_obj, number=number, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated


class RepositoryPullRequest(Entity):
    """A stored reference to a GitHub pull request for a repository."""

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="pull_requests",
        on_delete=models.CASCADE,
    )
    number = models.PositiveIntegerField()
    title = models.CharField(max_length=500)
    state = models.CharField(max_length=50)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    author = models.CharField(max_length=255, blank=True)
    labels = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    merged_at = models.DateTimeField(null=True, blank=True)
    source_branch = models.CharField(max_length=255, blank=True)
    target_branch = models.CharField(max_length=255, blank=True)
    is_draft = models.BooleanField(default=False)

    class Meta:
        ordering = ("-updated_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "number"],
                name="unique_pull_request_per_repository",
            )
        ]
        verbose_name = _("Repository Pull Request")
        verbose_name_plural = _("Repository Pull Requests")

    def __str__(self):  # pragma: no cover - simple representation
        return f"PR #{self.number} {self.title}".strip()

    @classmethod
    def fetch_open_pull_requests(
        cls, repository: GitHubRepository | None = None, token: str | None = None
    ) -> tuple[int, int]:
        repository = repository or GitHubRepository.resolve_active_repository()
        token = token or github_service.get_github_issue_token()
        repo_obj = github.ensure_repository(repository)

        created = 0
        updated = 0

        for item in github_service.fetch_repository_pull_requests(
            token=token, owner=repo_obj.owner, name=repo_obj.name
        ):
            number = item.get("number")
            if not isinstance(number, int):
                continue

            defaults = {
                "title": item.get("title") or "",
                "state": item.get("state") or "",
                "html_url": item.get("html_url") or "",
                "api_url": item.get("url") or "",
                "author": (item.get("user") or {}).get("login") or "",
                "labels": github.extract_label_names(item),
                "created_at": github.parse_github_timestamp(item.get("created_at")),
                "updated_at": github.parse_github_timestamp(item.get("updated_at")),
                "merged_at": github.parse_github_timestamp(item.get("merged_at"))
                if item.get("merged_at")
                else None,
                "source_branch": (item.get("head") or {}).get("ref") or "",
                "target_branch": (item.get("base") or {}).get("ref") or "",
                "is_draft": bool(item.get("draft")),
            }

            _, was_created = cls.objects.update_or_create(
                repository=repo_obj, number=number, defaults=defaults
            )
            if was_created:
                created += 1
            else:
                updated += 1

        return created, updated


class RepositoryWorkAssignment(Entity):
    """Operator assignment authorizing a node to work a repository issue or PR."""

    class TargetType(models.TextChoices):
        ISSUE = "issue", _("Issue")
        PULL_REQUEST = "pull_request", _("Pull Request")

    class Status(models.TextChoices):
        ASSIGNED = "assigned", _("Assigned")
        ACTIVE = "active", _("Active")
        REMOVED = "removed", _("Removed")

    repository = models.ForeignKey(
        GitHubRepository,
        related_name="work_assignments",
        on_delete=models.CASCADE,
    )
    target_type = models.CharField(max_length=20, choices=TargetType.choices)
    number = models.PositiveIntegerField()
    node = models.ForeignKey(
        "nodes.Node",
        related_name="repository_work_assignments",
        on_delete=models.CASCADE,
    )
    patchwork_authorized = models.BooleanField(default=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ASSIGNED,
    )
    reason = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        blank=True,
        null=True,
        related_name="repository_work_assignments",
        on_delete=models.SET_NULL,
    )
    assigned_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at", "-assigned_at", "-pk")
        constraints = [
            models.UniqueConstraint(
                fields=["repository", "target_type", "number", "node"],
                name="unique_repository_work_assignment_per_node",
            )
        ]
        verbose_name = _("Repository Work Assignment")
        verbose_name_plural = _("Repository Work Assignments")

    @property
    def is_assigned(self) -> bool:
        return self.status in {
            self.Status.ASSIGNED,
            self.Status.ACTIVE,
        }

    @property
    def is_active(self) -> bool:
        return self.patchwork_authorized and self.status == self.Status.ACTIVE

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.get_target_type_display()} #{self.number} -> {self.node}"


class RepositoryWorkNodeSnapshot(Entity):
    """Latest downstream developer metadata available to repository operators."""

    node = models.OneToOneField(
        "nodes.Node",
        related_name="repository_work_snapshot",
        on_delete=models.CASCADE,
    )
    capabilities = models.JSONField(default=dict, blank=True)
    current_load = models.JSONField(default=dict, blank=True)
    developer_info = models.JSONField(default=dict, blank=True)
    upstream_url = models.URLField(blank=True)
    reported_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-reported_at", "-updated_at", "node__hostname")
        verbose_name = _("Repository Work Node Snapshot")
        verbose_name_plural = _("Repository Work Node Snapshots")

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.node} repository work snapshot"
