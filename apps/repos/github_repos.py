from __future__ import annotations

import logging

from apps.repos.models import GitHubRepository
from apps.repos.services import github as github_service


logger = logging.getLogger(__name__)


def create_repository(
    owner: str | None,
    repo: str,
    *,
    visibility: str = "private",
    description: str | None = None,
):
    """Create a GitHub repository for the authenticated user or organisation."""

    repository = GitHubRepository(
        owner=owner or "", name=repo, description=description or "", is_private=visibility == "private"
    )

    response = github_service.create_repository(
        repository,
        package=None,
        private=repository.is_private,
        description=description,
    )
    logger.info(
        "GitHub repository created for %s at %s",
        owner or "authenticated user",
        response,
    )
    return response
