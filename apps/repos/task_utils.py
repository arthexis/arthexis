"""Helpers for reporting exceptions to GitHub and managing repositories."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apps.repos.models.repositories import GitHubRepository
from apps.repos.services import GitHubRepositoryError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.release.models import Package

logger = logging.getLogger(__name__)


def create_repository_for_package(
    package: Package,
    *,
    owner: str,
    repo: str,
    private: bool = False,
    description: str | None = None,
) -> str:
    """Create a GitHub repository and return its canonical URL."""

    repository = GitHubRepository(
        owner=owner,
        name=repo,
        description=description or "",
        is_private=private,
    )
    return repository.create_remote(
        package=package, private=private, description=description
    )
