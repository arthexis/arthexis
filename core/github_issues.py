from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

import requests

from .models import Package, PackageRelease
from .release import DEFAULT_PACKAGE


logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
LOCK_DIR = BASE_DIR / "locks" / "github-issues"
LOCK_TTL = timedelta(hours=1)
REQUEST_TIMEOUT = 10


def resolve_repository() -> tuple[str, str]:
    """Return the ``(owner, repo)`` tuple for the active package."""

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

    owner: str
    repo: str

    if repository_url.startswith("git@"):
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


def get_github_token() -> str:
    """Return the configured GitHub token.

    Preference is given to the latest :class:`~core.models.PackageRelease`.
    When unavailable, fall back to the ``GITHUB_TOKEN`` environment variable.
    """

    latest_release = PackageRelease.latest()
    if latest_release:
        token = latest_release.get_github_token()
        if token is not None:
            cleaned = token.strip() if isinstance(token, str) else str(token).strip()
            if cleaned:
                return cleaned

    env_token = os.environ.get("GITHUB_TOKEN")
    if env_token is not None:
        cleaned = env_token.strip() if isinstance(env_token, str) else str(env_token).strip()
        if cleaned:
            return cleaned

    raise RuntimeError("GitHub token is not configured")


def _ensure_lock_dir() -> None:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)


def _fingerprint_digest(fingerprint: str) -> str:
    return hashlib.sha256(str(fingerprint).encode("utf-8")).hexdigest()


def _fingerprint_path(fingerprint: str) -> Path:
    return LOCK_DIR / _fingerprint_digest(fingerprint)


def _has_recent_marker(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False

    marker_age = datetime.utcnow() - datetime.utcfromtimestamp(
        lock_path.stat().st_mtime
    )
    return marker_age < LOCK_TTL


def build_issue_payload(
    title: str,
    body: str,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
) -> Mapping[str, object] | None:
    """Return an API payload for GitHub issues.

    When ``fingerprint`` is provided, duplicate submissions within ``LOCK_TTL``
    are ignored by returning ``None``. A marker is kept on disk to prevent
    repeated reports during the cooldown window.
    """

    payload: dict[str, object] = {"title": title, "body": body}

    if labels:
        deduped = list(dict.fromkeys(labels))
        if deduped:
            payload["labels"] = deduped

    if fingerprint:
        _ensure_lock_dir()
        lock_path = _fingerprint_path(fingerprint)
        if _has_recent_marker(lock_path):
            logger.info("Skipping GitHub issue for active fingerprint %s", fingerprint)
            return None

        lock_path.write_text(datetime.utcnow().isoformat(), encoding="utf-8")
        digest = _fingerprint_digest(fingerprint)
        payload["body"] = f"{body}\n\n<!-- fingerprint:{digest} -->"

    return payload


def create_issue(
    title: str,
    body: str,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
) -> requests.Response | None:
    """Create a GitHub issue using the configured repository and token."""

    payload = build_issue_payload(title, body, labels=labels, fingerprint=fingerprint)
    if payload is None:
        return None

    owner, repo = resolve_repository()
    token = get_github_token()

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": "arthexis-runtime-reporter",
    }
    url = f"https://api.github.com/repos/{owner}/{repo}/issues"

    response = requests.post(
        url, json=payload, headers=headers, timeout=REQUEST_TIMEOUT
    )
    if not (200 <= response.status_code < 300):
        logger.error(
            "GitHub issue creation failed with status %s: %s",
            response.status_code,
            response.text,
        )
        response.raise_for_status()

    logger.info(
        "GitHub issue created for %s/%s with status %s",
        owner,
        repo,
        response.status_code,
    )
    return response
