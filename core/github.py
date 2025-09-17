"""Helpers for interacting with GitHub.

A GitHub personal access token should be supplied via the ``GITHUB_TOKEN``
environment variable so automated issue reports can be created.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

import requests

from core.release import DEFAULT_PACKAGE

logger = logging.getLogger(__name__)

DEFAULT_LABELS = ["automated-report", "start-script"]


def _repository_slug(payload: dict[str, Any]) -> str | None:
    if "repository" in payload and payload["repository"]:
        return str(payload["repository"])

    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        return repository

    parsed = urlparse(DEFAULT_PACKAGE.repository_url)
    slug = parsed.path.strip("/")
    return slug or None


def _build_title(payload: dict[str, Any]) -> str:
    fingerprint = str(payload.get("fingerprint", ""))
    fingerprint_short = fingerprint[:12] if fingerprint else "unknown"
    host = payload.get("host") or "unknown host"
    source = payload.get("source") or "unknown source"
    exit_code = payload.get("exit_code")
    exit_display = f"exit {exit_code}" if exit_code is not None else "exit ?"
    return f"{source} failure on {host} ({exit_display}) [{fingerprint_short}]"


def _build_body(payload: dict[str, Any]) -> str:
    sections = [
        f"**Source:** {payload.get('source', 'unknown')}",
        f"**Host:** {payload.get('host', 'unknown')}",
        f"**Version:** {payload.get('version', 'unknown')}",
        f"**Revision:** {payload.get('revision', '') or 'unknown'}",
        f"**Fingerprint:** {payload.get('fingerprint', '')}",
        f"**Exit code:** {payload.get('exit_code', 'unknown')}",
        f"**Command:** `{payload.get('command', '')}`",
        f"**Captured at:** {payload.get('captured_at', '')}",
    ]

    log_excerpt = payload.get("log_excerpt")
    if log_excerpt:
        sections.append("")
        sections.append("```")
        sections.append(str(log_excerpt))
        sections.append("```")

    return "\n".join(sections)


def submit_issue(payload: dict[str, Any]) -> None:
    """Create an issue in the configured GitHub repository.

    When the required authentication token is missing the helper simply logs a
    message and returns without raising so callers (notably Celery workers)
    continue running.
    """

    token = payload.get("token") or os.environ.get("GITHUB_TOKEN")
    if not token:
        logger.info("Skipping GitHub report because GITHUB_TOKEN is not configured")
        return

    repository = _repository_slug(payload)
    if not repository:
        logger.warning("Unable to determine repository for GitHub report")
        return

    issue_title = payload.get("title") or _build_title(payload)
    issue_body = payload.get("body") or _build_body(payload)
    labels = payload.get("labels") or DEFAULT_LABELS

    try:
        response = requests.post(
            f"https://api.github.com/repos/{repository}/issues",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": issue_title,
                "body": issue_body,
                "labels": labels,
            },
            timeout=payload.get("timeout", 10),
        )
        if response.status_code >= 400:
            logger.warning(
                "GitHub issue creation failed (%s): %s",
                response.status_code,
                response.text,
            )
    except requests.RequestException:
        logger.exception("Error while creating GitHub issue")
