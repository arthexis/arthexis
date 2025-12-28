"""Repository models."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity
from apps.repos import github
from apps.repos.services import github as github_service

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.release.models import Package


logger = logging.getLogger(__name__)


class GitHubRepository(Entity):
    """Source code repository reference specific to GitHub."""

    objects = github.GitHubRepositoryManager()
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

    @classmethod
    def from_url(cls, repository_url: str) -> "GitHubRepository":
        owner, repo = github.parse_repository_url(repository_url)
        return cls(owner=owner, name=repo)

    @classmethod
    def resolve_active_repository(cls) -> "GitHubRepository":
        """Return the ``(owner, repo)`` for the active package or default."""

        return github.resolve_active_repository(cls)

    @staticmethod
    def _resolve_token(package: Package | None) -> str:
        return github.resolve_repository_token(package)

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

    objects = github.PackageRepositoryManager()

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
