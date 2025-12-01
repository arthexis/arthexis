"""Helpers for creating GitHub issues using the domain model."""

from __future__ import annotations

from typing import Iterable, Mapping

from apps.repos.models import GitHubIssue


def resolve_repository() -> tuple[str, str]:
    """Return the ``(owner, repo)`` tuple for the active package."""

    repository = GitHubIssue.from_active_repository()
    return repository.owner, repository.repository


def get_github_token() -> str:
    """Return the configured GitHub token."""

    return GitHubIssue._get_github_token()


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
