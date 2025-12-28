"""GitHub helpers for repositories and issues."""

from __future__ import annotations

import contextlib
import logging
from datetime import datetime
from typing import Iterable, Mapping, TYPE_CHECKING

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.core.entity import EntityManager
from apps.repos.services import github as github_service
from apps.repos.services.github import GitHubIssue

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.release.models import Package
    from apps.repos.models.repositories import GitHubRepository


logger = logging.getLogger(__name__)


class GitHubRepositoryManager(EntityManager):
    def get_by_natural_key(self, owner: str, name: str):
        return self.get(owner=owner, name=name)


class PackageRepositoryManager(EntityManager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


def parse_repository_url(repository_url: str) -> tuple[str, str]:
    """Return the ``(owner, name)`` tuple from a repository URL."""

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


def repository_from_url(repository_url: str) -> "GitHubRepository":
    from apps.repos.models.repositories import GitHubRepository

    owner, repo = parse_repository_url(repository_url)
    return GitHubRepository(owner=owner, name=repo)


def resolve_active_repository(
    repository_cls: type["GitHubRepository"] | None = None,
) -> "GitHubRepository":
    """Return the repository for the active package or default."""

    from apps.release.models import Package
    from apps.release.release import DEFAULT_PACKAGE
    from apps.repos.models.repositories import GitHubRepository as RepositoryModel

    repository_cls = repository_cls or RepositoryModel
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

    return repository_cls.from_url(repository_url)


def resolve_repository_token(package: Package | None) -> str:
    return github_service.resolve_repository_token(package)


def parse_github_timestamp(value: str | None) -> datetime:
    parsed = parse_datetime(value) if value else None
    if parsed is None:
        parsed = timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    return parsed


def ensure_repository(repository: "GitHubRepository") -> "GitHubRepository":
    from apps.repos.models.repositories import GitHubRepository as RepositoryModel

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
    repo_obj, _ = RepositoryModel.objects.get_or_create(
        owner=repository.owner, name=repository.name, defaults=defaults
    )
    return repo_obj


# Issue helpers

def resolve_issue_repository() -> tuple[str, str]:
    """Return the ``(owner, repo)`` tuple for the active package."""

    repository = GitHubIssue.from_active_repository()
    return repository.owner, repository.repository


def get_github_token() -> str:
    """Return the configured GitHub token."""

    return github_service.get_github_issue_token()


def build_issue_payload(
    title: str,
    body: str,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
) -> Mapping[str, object] | None:
    """Return an API payload for GitHub issues."""

    issue = GitHubIssue.from_active_repository()
    return issue._build_issue_payload(title, body, labels=labels, fingerprint=fingerprint)


def create_issue(
    title: str,
    body: str,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
):
    """Create a GitHub issue using the configured repository and token."""

    issue = GitHubIssue.from_active_repository()
    return issue.create(title, body, labels=labels, fingerprint=fingerprint)


# Repository helpers

def create_repository(
    owner: str | None,
    repo: str,
    *,
    visibility: str = "private",
    description: str | None = None,
):
    """Create a GitHub repository for the authenticated user or organisation."""

    from apps.repos.models.repositories import GitHubRepository

    repository = GitHubRepository(
        owner=owner or "", name=repo, description=description or "", is_private=visibility == "private"
    )

    package = None
    with contextlib.suppress(Exception):
        from apps.release.models import PackageRelease

        latest_release = PackageRelease.latest()
        if latest_release:
            package = getattr(latest_release, "package", None)

    response = github_service.create_repository(
        repository,
        package=package,
        private=repository.is_private,
        description=description,
    )
    logger.info(
        "GitHub repository created for %s at %s",
        owner or "authenticated user",
        response,
    )
    return response
