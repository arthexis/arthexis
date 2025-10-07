"""Helpers for reporting exceptions to GitHub and managing repositories."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from celery import shared_task
import requests


if TYPE_CHECKING:  # pragma: no cover - typing only
    from .models import Package


logger = logging.getLogger(__name__)


GITHUB_API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT = 10


class GitHubRepositoryError(RuntimeError):
    """Raised when a GitHub repository operation fails."""



@shared_task
def report_exception_to_github(payload: dict[str, Any]) -> None:
    """Send exception context to the GitHub issue helper.

    The task is intentionally light-weight in this repository. Deployments can
    replace it with an implementation that forwards ``payload`` to the
    automation responsible for creating GitHub issues.
    """

    logger.info(
        "Queued GitHub issue report for %s", payload.get("fingerprint", "<unknown>")
    )


def _resolve_github_token(package: Package | None) -> str:
    """Return the GitHub token for ``package``.

    Preference is given to the release manager associated with the package.
    When unavailable, fall back to the ``GITHUB_TOKEN`` environment variable.
    """

    if package:
        manager = getattr(package, "release_manager", None)
        if manager:
            token = getattr(manager, "github_token", "")
            if token:
                cleaned = str(token).strip()
                if cleaned:
                    return cleaned

    token = os.environ.get("GITHUB_TOKEN", "")
    cleaned_env = token.strip() if isinstance(token, str) else str(token).strip()
    if not cleaned_env:
        raise GitHubRepositoryError("GitHub token is not configured")
    return cleaned_env


def _build_headers(token: str) -> Mapping[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": "arthexis-admin",
    }


def _build_payload(repo: str, *, private: bool, description: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": repo, "private": private}
    if description:
        payload["description"] = description
    return payload


def _extract_error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        data = {}

    message = data.get("message") or response.text or "GitHub repository request failed"
    errors = data.get("errors")
    details: list[str] = []
    if isinstance(errors, list):
        for entry in errors:
            if isinstance(entry, str):
                details.append(entry)
            elif isinstance(entry, Mapping):
                text = entry.get("message") or entry.get("code")
                if text:
                    details.append(str(text))

    if details:
        message = f"{message} ({'; '.join(details)})"

    return message


def _safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {}
    return data


def create_repository_for_package(
    package: Package,
    *,
    owner: str,
    repo: str,
    private: bool = False,
    description: str | None = None,
) -> str:
    """Create a GitHub repository and return its canonical URL.

    The helper attempts to create the repository under ``owner`` when provided.
    If the authenticated token lacks access to the organization, the helper
    falls back to creating the repository for the authenticated user. On
    success, the GitHub HTML URL for the repository is returned.
    """

    token = _resolve_github_token(package)
    headers = _build_headers(token)
    payload = _build_payload(repo, private=private, description=description)

    endpoints: list[str] = []
    owner = owner.strip()
    if owner:
        endpoints.append(f"{GITHUB_API_ROOT}/orgs/{owner}/repos")
    endpoints.append(f"{GITHUB_API_ROOT}/user/repos")

    last_error: str | None = None

    for index, endpoint in enumerate(endpoints):
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            logger.exception(
                "GitHub repository creation request failed for %s/%s", owner, repo
            )
            raise GitHubRepositoryError(str(exc)) from exc

        if 200 <= response.status_code < 300:
            data = _safe_json(response)
            html_url = data.get("html_url")
            if html_url:
                return html_url

            resolved_owner = (
                data.get("owner", {}).get("login")
                if isinstance(data.get("owner"), Mapping)
                else owner
            )
            resolved_owner = (resolved_owner or owner).strip("/")
            return f"https://github.com/{resolved_owner}/{repo}"

        message = _extract_error_message(response)
        logger.error(
            "GitHub repository creation failed for %s/%s (%s): %s",
            owner or "<user>",
            repo,
            response.status_code,
            message,
        )
        last_error = message

        # If we're attempting to create within an organization and receive a
        # not found or forbidden error, fall back to creating for the
        # authenticated user.
        if index == 0 and owner and response.status_code in {403, 404}:
            continue

        break

    raise GitHubRepositoryError(last_error or "GitHub repository creation failed")
