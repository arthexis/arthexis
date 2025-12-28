from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from apps.release.models import Package


logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT = 10
ISSUE_LOCK_DIR = Path(__file__).resolve().parent.parent / ".locks" / "github-issues"
ISSUE_LOCK_TTL = timedelta(hours=1)


class GitHubRepositoryError(RuntimeError):
    """Raised when a GitHub repository operation fails."""


def build_headers(token: str, *, user_agent: str = "arthexis-admin") -> Mapping[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": user_agent,
    }


def _get_latest_release_token() -> str | None:
    """Return the GitHub token from the latest package release, if available."""

    try:
        from apps.release.models import PackageRelease
    except Exception:  # pragma: no cover - optional dependency during service-only tests
        return None

    latest_release = PackageRelease.latest()
    if latest_release:
        token = latest_release.get_github_token()
        if token is not None:
            cleaned = token.strip() if isinstance(token, str) else str(token).strip()
            if cleaned:
                return cleaned
    return None


def resolve_repository_token(package: Package | None) -> str:
    """Return the GitHub token for ``package`` or the environment."""

    def _clean_token(token: object | None) -> str:
        if token is None:
            return ""
        return token.strip() if isinstance(token, str) else str(token).strip()

    if package:
        manager = getattr(package, "release_manager", None)
        if manager:
            token = _clean_token(getattr(manager, "github_token", ""))
            if token:
                return token

    release_token = _get_latest_release_token()
    if release_token:
        return release_token

    token = os.environ.get("GITHUB_TOKEN", "")
    cleaned_env = _clean_token(token)
    if not cleaned_env:
        raise GitHubRepositoryError("GitHub token is not configured")
    return cleaned_env


def _build_repository_payload(
    name: str, *, private: bool, description: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "private": private}
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


def _safe_json(response: requests.Response) -> dict[str, Any] | list[Any]:
    try:
        return response.json()
    except ValueError:
        return {}


def create_repository(
    repository: Any,
    *,
    package: Package | None,
    private: bool | None = None,
    description: str | None = None,
    api_root: str = API_ROOT,
    timeout: int = REQUEST_TIMEOUT,
) -> str:
    """Create the repository on GitHub and return its HTML URL."""

    token = resolve_repository_token(package)
    headers = build_headers(token)
    payload = _build_repository_payload(
        getattr(repository, "name", ""),
        private=getattr(repository, "is_private", False) if private is None else private,
        description=description if description is not None else getattr(repository, "description", ""),
    )

    endpoints: list[str] = []
    owner = (getattr(repository, "owner", "") or "").strip()
    if owner:
        endpoints.append(f"{api_root}/orgs/{owner}/repos")
    endpoints.append(f"{api_root}/user/repos")

    last_error: str | None = None

    for index, endpoint in enumerate(endpoints):
        response = None
        try:
            response = requests.post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            logger.exception(
                "GitHub repository creation request failed for %s", getattr(repository, "slug", None)
            )
            raise GitHubRepositoryError(str(exc)) from exc

        try:
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
                return f"https://github.com/{resolved_owner}/{getattr(repository, 'name', '')}"

            message = _extract_error_message(response)
            logger.error(
                "GitHub repository creation failed for %s (%s): %s",
                getattr(repository, "slug", None) or "<user>/<repo>",
                response.status_code,
                message,
            )
            last_error = message

            if index == 0 and owner and response.status_code in {403, 404}:
                continue

            break
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()

    raise GitHubRepositoryError(last_error or "GitHub repository creation failed")


def fetch_paginated_items(
    *,
    token: str,
    endpoint: str,
    params: Mapping[str, object],
    timeout: int = REQUEST_TIMEOUT,
) -> Iterator[Mapping[str, object]]:
    headers = build_headers(token)
    url = endpoint
    query_params: Mapping[str, object] | None = params

    while url:
        response = None
        try:
            response = requests.get(
                url,
                headers=headers,
                params=query_params,
                timeout=timeout,
            )
            query_params = None

            if not (200 <= response.status_code < 300):
                message = _extract_error_message(response)
                raise GitHubRepositoryError(message)

            data = _safe_json(response)
            if isinstance(data, list):
                for entry in data:
                    if isinstance(entry, Mapping):
                        yield entry

            links = getattr(response, "links", {}) or {}
            url = links.get("next", {}).get("url")
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()


def fetch_repository_issues(
    *,
    token: str,
    owner: str,
    name: str,
    state: str = "open",
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/issues"
    params = {"state": state, "per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def fetch_repository_pull_requests(
    *,
    token: str,
    owner: str,
    name: str,
    state: str = "open",
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/pulls"
    params = {"state": state, "per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def _ensure_issue_lock_dir() -> None:
    ISSUE_LOCK_DIR.mkdir(parents=True, exist_ok=True)


def _issue_fingerprint_digest(fingerprint: str) -> str:
    return hashlib.sha256(str(fingerprint).encode("utf-8")).hexdigest()


def _issue_fingerprint_path(fingerprint: str) -> Path:
    return ISSUE_LOCK_DIR / _issue_fingerprint_digest(fingerprint)


def _issue_has_recent_marker(lock_path: Path) -> bool:
    if not lock_path.exists():
        return False

    marker_age = datetime.now(timezone.utc) - datetime.fromtimestamp(
        lock_path.stat().st_mtime, timezone.utc
    )
    return marker_age < ISSUE_LOCK_TTL


def build_issue_payload(
    title: str,
    body: str,
    *,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
) -> Mapping[str, object] | None:
    payload: dict[str, object] = {"title": title, "body": body}

    if labels:
        deduped = list(dict.fromkeys(labels))
        if deduped:
            payload["labels"] = deduped

    if fingerprint:
        _ensure_issue_lock_dir()
        lock_path = _issue_fingerprint_path(fingerprint)
        if _issue_has_recent_marker(lock_path):
            logger.info("Skipping GitHub issue for active fingerprint %s", fingerprint)
            return None

        lock_path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        digest = _issue_fingerprint_digest(fingerprint)
        payload["body"] = f"{body}\n\n<!-- fingerprint:{digest} -->"

    return payload


def get_github_issue_token() -> str:
    """Return the configured GitHub token for issue reporting."""

    from apps.release.models import PackageRelease
    from apps.release.release import DEFAULT_PACKAGE

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

    raise RuntimeError(f"GitHub token is not configured; set one via {DEFAULT_PACKAGE.repository_url}")


def create_issue(
    owner: str,
    repository: str,
    *,
    token: str,
    title: str,
    body: str,
    labels: Iterable[str] | None = None,
    fingerprint: str | None = None,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response | None:
    payload = build_issue_payload(title, body, labels=labels, fingerprint=fingerprint)
    if payload is None:
        return None

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    url = f"{API_ROOT}/repos/{owner}/{repository}/issues"

    response = None
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except Exception:
        if response is not None:
            with contextlib.suppress(Exception):
                response.close()
        raise

    if not (200 <= response.status_code < 300):
        logger.error(
            "GitHub issue creation failed with status %s: %s",
            response.status_code,
            response.text,
        )
        try:
            response.raise_for_status()
        finally:
            with contextlib.suppress(Exception):
                response.close()
        return None

    logger.info(
        "GitHub issue created for %s/%s with status %s",
        owner,
        repository,
        response.status_code,
    )
    return response


@dataclass(slots=True)
class GitHubIssue:
    """Represents a GitHub issue creation request."""

    owner: str
    repository: str
    token: str

    BASE_DIR = Path(__file__).resolve().parent.parent
    LOCK_DIR = ISSUE_LOCK_DIR
    LOCK_TTL = ISSUE_LOCK_TTL
    REQUEST_TIMEOUT = REQUEST_TIMEOUT

    @classmethod
    def from_active_repository(cls) -> "GitHubIssue":
        from apps.repos.models.repositories import GitHubRepository

        repository = GitHubRepository.resolve_active_repository()
        token = get_github_issue_token()
        return cls(repository.owner, repository.name, token)

    @classmethod
    def _get_github_token(cls) -> str:
        return get_github_issue_token()

    @staticmethod
    def _ensure_lock_dir() -> None:
        _ensure_issue_lock_dir()

    @staticmethod
    def _fingerprint_digest(fingerprint: str) -> str:
        return _issue_fingerprint_digest(fingerprint)

    @staticmethod
    def _fingerprint_path(fingerprint: str) -> Path:
        return _issue_fingerprint_path(fingerprint)

    @staticmethod
    def _has_recent_marker(lock_path: Path) -> bool:
        return _issue_has_recent_marker(lock_path)

    def _build_issue_payload(
        self,
        title: str,
        body: str,
        labels: Iterable[str] | None = None,
        fingerprint: str | None = None,
    ) -> Mapping[str, object] | None:
        return build_issue_payload(title, body, labels=labels, fingerprint=fingerprint)

    def create(
        self,
        title: str,
        body: str,
        labels: Iterable[str] | None = None,
        fingerprint: str | None = None,
    ) -> requests.Response | None:
        return create_issue(
            self.owner,
            self.repository,
            token=self.token,
            title=title,
            body=body,
            labels=labels,
            fingerprint=fingerprint,
            timeout=self.REQUEST_TIMEOUT,
        )
