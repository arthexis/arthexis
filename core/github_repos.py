from __future__ import annotations

import logging
from typing import Mapping

import requests

from .github_issues import REQUEST_TIMEOUT, get_github_token


logger = logging.getLogger(__name__)


def _build_repository_payload(
    repo: str,
    visibility: str,
    description: str | None,
) -> Mapping[str, object]:
    payload: dict[str, object] = {"name": repo, "visibility": visibility}

    if description is not None:
        payload["description"] = description

    return payload


def create_repository(
    owner: str | None,
    repo: str,
    *,
    visibility: str = "private",
    description: str | None = None,
) -> requests.Response:
    """Create a GitHub repository for the authenticated user or organisation."""

    token = get_github_token()

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": "arthexis-runtime-reporter",
    }

    if owner:
        url = f"https://api.github.com/orgs/{owner}/repos"
    else:
        url = "https://api.github.com/user/repos"

    payload = _build_repository_payload(repo, visibility, description)

    response = requests.post(
        url,
        json=payload,
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )

    if not (200 <= response.status_code < 300):
        logger.error(
            "GitHub repository creation failed with status %s: %s",
            response.status_code,
            response.text,
        )
        response.raise_for_status()

    logger.info(
        "GitHub repository created for %s with status %s",
        owner or "authenticated user",
        response.status_code,
    )

    return response
