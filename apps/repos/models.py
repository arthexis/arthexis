"""Domain models for repositories and GitHub issues."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity, EntityManager
from apps.repos.services import github as github_service
from apps.repos.services.github import GitHubIssue, GitHubRepositoryError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.release.models import Package


logger = logging.getLogger(__name__)


class GitHubRepositoryManager(EntityManager):
    def get_by_natural_key(self, owner: str, name: str):
        return self.get(owner=owner, name=name)


class PackageRepositoryManager(EntityManager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class GitHubRepository(Entity):
    """Source code repository reference specific to GitHub."""

    objects = GitHubRepositoryManager()
    API_ROOT = github_service.API_ROOT
    REQUEST_TIMEOUT = github_service.REQUEST_TIMEOUT

    owner = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_private = models.BooleanField(default=False)
    html_url = models.URLField(blank=True)
    api_url = models.URLField(blank=True)
    ssh_url = models.CharField(max_length=255, blank=True)
    default_branch = models.CharField(max_length=100, blank=True)

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.owner, self.name)

    @property
    def slug(self):  # pragma: no cover - simple representation
        return f"{self.owner}/{self.name}".strip("/")

    def __str__(self):  # pragma: no cover - simple representation
        return self.slug

    @staticmethod
    def _parse_repository_url(repository_url: str) -> tuple[str, str]:
        repository_url = (repository_url or "").strip()
        if repository_url.startswith("git@"):  # pragma: no cover - convenience
            _, _, remainder = repository_url.partition(":")
            path = remainder
        else:
            from urllib.parse import urlparse

            parsed = urlparse(repository_url)
            path = parsed.path

        path = path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]

        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 2:
            raise ValueError(f"Invalid repository URL: {repository_url!r}")

        owner, repo = segments[-2], segments[-1]
        return owner, repo

    @classmethod
    def from_url(cls, repository_url: str) -> GitHubRepository:
        owner, repo = cls._parse_repository_url(repository_url)
        return cls(owner=owner, name=repo)

    @classmethod
    def resolve_active_repository(cls) -> GitHubRepository:
        """Return the ``(owner, repo)`` for the active package or default."""

        from apps.release.models import Package
        from apps.release.release import DEFAULT_PACKAGE

        package = Package.objects.filter(is_active=True).first()

        repository_url: str
        if package is not None:
            raw_url = getattr(package, "repository_url", "")
            if raw_url is None:
                cleaned_url = ""
            else:
                cleaned_url = str(raw_url).strip()
            repository_url = cleaned_url or DEFAULT_PACKAGE.repository_url
        else:
            repository_url = DEFAULT_PACKAGE.repository_url

        return cls.from_url(repository_url)

    @staticmethod
    def _resolve_token(package: Package | None) -> str:
        return github_service.resolve_repository_token(package)

    def create_remote(
        self,
        *,
        package: Package | None,
        private: bool | None = None,
        description: str | None = None,
    ) -> str:
        """Create the repository on GitHub and return its HTML URL."""

        return github_service.create_repository(
            self, package=package, private=private, description=description
        )

    class Meta:
        verbose_name = _("GitHub Repository")
        verbose_name_plural = _("GitHub Repositories")
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "name"], name="unique_github_repository_owner_name"
            )
        ]


class PackageRepository(Entity):
    """Represents a package upload target such as PyPI."""

    objects = PackageRepositoryManager()

    name = models.CharField(max_length=255, unique=True)
    repository_url = models.URLField(blank=True, default="")
    verify_availability = models.BooleanField(default=False)
    extra_args = models.JSONField(default=list, blank=True)
    token = models.CharField(max_length=255, blank=True, default="")
    username = models.CharField(max_length=150, blank=True, default="")
    password = models.CharField(max_length=150, blank=True, default="")
    packages = models.ManyToManyField(
        "release.Package",
        related_name="package_repositories",
        blank=True,
    )

    def natural_key(self):
        return (self.name,)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    def to_target(self):
        from apps.release.release import Credentials, RepositoryTarget

        token = (self.token or "").strip()
        username = (self.username or "").strip()
        password = (self.password or "").strip()

        credentials = None
        if token or (username and password):
            credentials = Credentials(
                token=token or None,
                username=username or None,
                password=password or None,
            )

        return RepositoryTarget(
            name=self.name,
            repository_url=(self.repository_url or None),
            credentials=credentials,
            verify_availability=self.verify_availability,
            extra_args=tuple(self.extra_args or ()),
        )

    class Meta:
        ordering = ("name",)
        verbose_name = _("Package Repository")
        verbose_name_plural = _("Package Repositories")


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

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime:
        parsed = parse_datetime(value) if value else None
        if parsed is None:
            parsed = timezone.now()
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.utc)
        return parsed

    @staticmethod
    def _ensure_repository(repository: GitHubRepository) -> GitHubRepository:
        if repository.pk:
            return repository

        defaults = {
            "description": getattr(repository, "description", ""),
            "is_private": getattr(repository, "is_private", False),
            "html_url": getattr(repository, "html_url", ""),
            "api_url": getattr(repository, "api_url", ""),
            "ssh_url": getattr(repository, "ssh_url", ""),
            "default_branch": getattr(repository, "default_branch", ""),
        }
        repo_obj, _ = GitHubRepository.objects.get_or_create(
            owner=repository.owner, name=repository.name, defaults=defaults
        )
        return repo_obj

    @classmethod
    def fetch_open_issues(
        cls, repository: GitHubRepository | None = None, token: str | None = None
    ) -> tuple[int, int]:
        repository = repository or GitHubRepository.resolve_active_repository()
        token = token or github_service.get_github_issue_token()
        repo_obj = cls._ensure_repository(repository)

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
                "created_at": cls._parse_timestamp(item.get("created_at")),
                "updated_at": cls._parse_timestamp(item.get("updated_at")),
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
        repo_obj = RepositoryIssue._ensure_repository(repository)

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
                "created_at": RepositoryIssue._parse_timestamp(item.get("created_at")),
                "updated_at": RepositoryIssue._parse_timestamp(item.get("updated_at")),
                "merged_at": RepositoryIssue._parse_timestamp(item.get("merged_at"))
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
