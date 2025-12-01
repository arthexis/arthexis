from __future__ import annotations

import logging

from apps.repos.github_issues import get_github_token
from apps.repos.models import GitHubRepository


logger = logging.getLogger(__name__)


def create_repository(
    owner: str | None,
    repo: str,
    *,
    visibility: str = "private",
    description: str | None = None,
):
    """Create a GitHub repository for the authenticated user or organisation."""

    token = get_github_token()
    repository = GitHubRepository(
        owner=owner or "", name=repo, description=description or "", is_private=visibility == "private"
    )

    headers = GitHubRepository._build_headers(token)
    payload = repository._build_payload(private=repository.is_private, description=description)

    if owner:
        endpoint = f"{GitHubRepository.API_ROOT}/orgs/{owner}/repos"
    else:
        endpoint = f"{GitHubRepository.API_ROOT}/user/repos"

    response = repository._make_request(endpoint, payload, headers)
    logger.info(
        "GitHub repository created for %s with status %s",
        owner or "authenticated user",
        response.status_code,
    )
    return response
