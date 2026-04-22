from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Protocol, TypeAlias

import requests
from django.conf import settings

if TYPE_CHECKING:
    from apps.release.models import Package


logger = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
REQUEST_TIMEOUT = 10
ISSUE_LOCK_TTL = timedelta(hours=1)


def _resolve_issue_lock_dir() -> Path:
    """Return the project-level lock directory for GitHub issue fingerprints."""

    with contextlib.suppress(Exception):
        configured_base_dir = getattr(settings, "BASE_DIR", None)
        if configured_base_dir:
            return Path(configured_base_dir) / ".locks" / "github-issues"

    # Fallback for imports that happen before Django settings are configured.
    return Path(__file__).resolve().parents[3] / ".locks" / "github-issues"


ISSUE_LOCK_DIR = _resolve_issue_lock_dir()


class GitHubRepositoryError(RuntimeError):
    """Raised when a GitHub repository operation fails."""


class SupportsRepositoryPayload(Protocol):
    """Repository-like object accepted by the GitHub service helpers."""

    name: str
    owner: str
    description: str
    is_private: bool


JSONScalar: TypeAlias = str | int | float | bool | None
JSONMapping: TypeAlias = dict[str, Any]
JSONList: TypeAlias = list[Any]
JSONValue: TypeAlias = JSONMapping | JSONList | JSONScalar
RequestParamScalar: TypeAlias = str | bytes | int | float
RequestParamValue: TypeAlias = RequestParamScalar | Iterable[RequestParamScalar] | None
RequestParams: TypeAlias = Mapping[str, RequestParamValue]


def build_headers(token: str, *, user_agent: str = "arthexis-admin") -> Mapping[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {token}",
        "User-Agent": user_agent,
    }


def validate_token(
    token: str,
    *,
    api_root: str = API_ROOT,
    timeout: int = REQUEST_TIMEOUT,
) -> tuple[bool, str, str]:
    """Validate a GitHub token against the current user endpoint."""

    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        return False, "Enter a GitHub token before testing.", ""

    try:
        response = requests.get(
            f"{api_root}/user",
            headers=build_headers(cleaned_token),
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        return False, str(exc), ""

    if not 200 <= response.status_code < 300:
        return False, _extract_error_message(response), ""

    payload = _safe_json(response)
    payload_data = payload if isinstance(payload, Mapping) else {}
    login = str(payload_data.get("login") or "").strip()
    if login:
        return True, f"Connected to GitHub as {login}.", login
    return True, "Connected to GitHub.", ""


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

    release_token = _get_latest_release_token()
    if release_token:
        return release_token

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
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


def _safe_json(response: requests.Response) -> JSONValue:
    """Return parsed JSON for ``response`` and require callers to narrow before indexing.

    Parameters:
        response: HTTP response whose JSON payload should be decoded.

    Returns:
        The decoded JSON payload, which may be a mapping, list, scalar, or ``None``.

    Raises:
        No exceptions are raised. Invalid JSON payloads fall back to an empty mapping.
    """

    try:
        return response.json()
    except ValueError:
        return {}


def create_repository(
    repository: SupportsRepositoryPayload,
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
                payload_data = data if isinstance(data, dict) else {}
                html_url = payload_data.get("html_url")
                if html_url:
                    return html_url

                owner_data = payload_data.get("owner")
                resolved_owner = (
                    owner_data.get("login") if isinstance(owner_data, Mapping) else owner
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
    params: RequestParams,
    timeout: int = REQUEST_TIMEOUT,
) -> Iterator[Mapping[str, object]]:
    headers = build_headers(token)
    url = endpoint
    query_params: RequestParams | None = params

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
    params: dict[str, RequestParamValue] = {"state": state, "per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def fetch_repository_pull_requests(
    *,
    token: str,
    owner: str,
    name: str,
    state: str = "open",
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/pulls"
    params: dict[str, RequestParamValue] = {"state": state, "per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def fetch_issue_comments(
    *,
    token: str,
    owner: str,
    name: str,
    issue_number: int,
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/issues/{issue_number}/comments"
    params: dict[str, RequestParamValue] = {"per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def _fetch_json_mapping(
    *,
    token: str,
    endpoint: str,
    timeout: int,
    decode_error: str,
) -> Mapping[str, object]:
    headers = build_headers(token)
    response = None
    try:
        try:
            response = requests.get(endpoint, headers=headers, timeout=timeout)
        except requests.RequestException as exc:  # pragma: no cover - network failure
            raise GitHubRepositoryError(str(exc)) from exc

        if not (200 <= response.status_code < 300):
            raise GitHubRepositoryError(_extract_error_message(response))

        payload = _safe_json(response)
        if isinstance(payload, Mapping):
            return payload
        raise GitHubRepositoryError(decode_error)
    finally:
        if response is not None:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()


def fetch_issue_or_pull_request(
    *,
    token: str,
    owner: str,
    name: str,
    number: int,
    timeout: int = REQUEST_TIMEOUT,
) -> Mapping[str, object]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/issues/{number}"
    return _fetch_json_mapping(
        token=token,
        endpoint=endpoint,
        timeout=timeout,
        decode_error="Unable to decode issue details from GitHub",
    )


def fetch_commit_status_summary(
    *,
    token: str,
    owner: str,
    name: str,
    sha: str,
    timeout: int = REQUEST_TIMEOUT,
) -> Mapping[str, object]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/commits/{sha}/status"
    return _fetch_json_mapping(
        token=token,
        endpoint=endpoint,
        timeout=timeout,
        decode_error="Unable to decode commit status from GitHub",
    )


def fetch_pull_request(
    *,
    token: str,
    owner: str,
    name: str,
    number: int,
    timeout: int = REQUEST_TIMEOUT,
) -> Mapping[str, object]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/pulls/{number}"
    return _fetch_json_mapping(
        token=token,
        endpoint=endpoint,
        timeout=timeout,
        decode_error="Unable to decode pull request details from GitHub",
    )


def fetch_pull_request_reviews(
    *,
    token: str,
    owner: str,
    name: str,
    number: int,
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/pulls/{number}/reviews"
    params: dict[str, RequestParamValue] = {"per_page": 100}
    yield from fetch_paginated_items(token=token, endpoint=endpoint, params=params)


def fetch_pull_request_review_comments(
    *,
    token: str,
    owner: str,
    name: str,
    number: int,
) -> Iterator[Mapping[str, object]]:
    endpoint = f"{API_ROOT}/repos/{owner}/{name}/pulls/{number}/comments"
    params: dict[str, RequestParamValue] = {"per_page": 100}
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
    from apps.release import DEFAULT_PACKAGE

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

    raise GitHubRepositoryError(
        f"GitHub token is not configured; set one via {DEFAULT_PACKAGE.repository_url}"
    )


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


def _pull_request_is_open(
    owner: str,
    repository: str,
    *,
    pull_number: int,
    headers: Mapping[str, str],
    timeout: int,
) -> bool:
    """Return whether a pull request currently has ``open`` state."""

    url = f"{API_ROOT}/repos/{owner}/{repository}/pulls/{pull_number}"
    response = None
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    try:
        if not (200 <= response.status_code < 300):
            raise GitHubRepositoryError(_extract_error_message(response))

        data = _safe_json(response)
        if not isinstance(data, Mapping):
            return False
        return str(data.get("state") or "").lower() == "open"
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def create_pull_request_comment(
    owner: str,
    repository: str,
    *,
    pull_number: int,
    token: str,
    body: str,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    """Post a comment on a specific open pull request and return the API response."""

    cleaned_body = body.strip()
    if not cleaned_body:
        raise ValueError("Pull request comment body must not be empty")

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    if not _pull_request_is_open(
        owner,
        repository,
        pull_number=pull_number,
        headers=headers,
        timeout=timeout,
    ):
        raise GitHubRepositoryError(
            f"Cannot comment on PR #{pull_number} because it is not open"
        )

    url = f"{API_ROOT}/repos/{owner}/{repository}/issues/{pull_number}/comments"
    response = None
    try:
        response = requests.post(
            url,
            json={"body": cleaned_body},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    if not (200 <= response.status_code < 300):
        try:
            message = _extract_error_message(response)
        finally:
            with contextlib.suppress(Exception):
                response.close()
        raise GitHubRepositoryError(message)

    logger.info(
        "GitHub pull request comment created for %s/%s#%s with status %s",
        owner,
        repository,
        pull_number,
        response.status_code,
    )
    return response


def submit_pull_request_review_decision(
    *,
    owner: str,
    repository: str,
    pull_number: int,
    token: str,
    decision: str,
    body: str = "",
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    """Submit a pull request review decision for an open PR."""

    normalized_decision = str(decision or "").strip().upper()
    allowed_decisions = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
    if normalized_decision not in allowed_decisions:
        raise ValueError("Review decision must be APPROVE, REQUEST_CHANGES, or COMMENT")

    cleaned_body = body.strip()
    if normalized_decision in {"REQUEST_CHANGES", "COMMENT"} and not cleaned_body:
        raise ValueError(
            "Review body is required for request changes and comment decisions"
        )

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    if not _pull_request_is_open(
        owner,
        repository,
        pull_number=pull_number,
        headers=headers,
        timeout=timeout,
    ):
        raise GitHubRepositoryError(
            f"Cannot review PR #{pull_number} because it is not open"
        )

    payload: dict[str, str] = {"event": normalized_decision}
    if cleaned_body:
        payload["body"] = cleaned_body

    url = f"{API_ROOT}/repos/{owner}/{repository}/pulls/{pull_number}/reviews"
    response = None
    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    if not (200 <= response.status_code < 300):
        try:
            message = _extract_error_message(response)
        finally:
            with contextlib.suppress(Exception):
                response.close()
        raise GitHubRepositoryError(message)

    logger.info(
        "GitHub pull request review submitted for %s/%s#%s with decision %s",
        owner,
        repository,
        pull_number,
        normalized_decision,
    )
    return response


def merge_pull_request(
    *,
    owner: str,
    repository: str,
    pull_number: int,
    token: str,
    merge_method: str = "squash",
    commit_title: str = "",
    commit_message: str = "",
    timeout: int = REQUEST_TIMEOUT,
) -> Mapping[str, object]:
    """Merge a pull request when GitHub reports it is mergeable."""

    method = str(merge_method or "").strip().lower()
    if method not in {"merge", "squash", "rebase"}:
        raise ValueError("Merge method must be merge, squash, or rebase")

    pull = fetch_pull_request(
        token=token,
        owner=owner,
        name=repository,
        number=pull_number,
        timeout=timeout,
    )
    if str(pull.get("state") or "").lower() != "open":
        raise GitHubRepositoryError(f"Cannot merge PR #{pull_number} because it is not open")

    mergeable = pull.get("mergeable")
    if mergeable is None:
        raise GitHubRepositoryError(
            f"Cannot merge PR #{pull_number} because mergeability is still being calculated"
        )
    if mergeable is not True:
        mergeable_state = str(pull.get("mergeable_state") or "unknown")
        raise GitHubRepositoryError(
            f"Cannot merge PR #{pull_number} while mergeable state is '{mergeable_state}'"
        )

    payload: dict[str, str] = {"merge_method": method}
    cleaned_title = commit_title.strip()
    cleaned_message = commit_message.strip()
    if cleaned_title:
        payload["commit_title"] = cleaned_title
    if cleaned_message:
        payload["commit_message"] = cleaned_message

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    url = f"{API_ROOT}/repos/{owner}/{repository}/pulls/{pull_number}/merge"
    response = None
    try:
        response = requests.put(url, json=payload, headers=headers, timeout=timeout)
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    try:
        if not (200 <= response.status_code < 300):
            raise GitHubRepositoryError(_extract_error_message(response))
        body = _safe_json(response)
        if isinstance(body, Mapping):
            return body
        raise GitHubRepositoryError("Unable to decode merge response from GitHub")
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close()


def create_issue_comment(
    owner: str,
    repository: str,
    *,
    issue_number: int,
    token: str,
    body: str,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    """Post a comment on a specific issue or pull request and return the API response."""

    cleaned_body = body.strip()
    if not cleaned_body:
        raise ValueError("Issue comment body must not be empty")

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    url = f"{API_ROOT}/repos/{owner}/{repository}/issues/{issue_number}/comments"
    response = None
    try:
        response = requests.post(
            url,
            json={"body": cleaned_body},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    if not (200 <= response.status_code < 300):
        try:
            message = _extract_error_message(response)
        finally:
            with contextlib.suppress(Exception):
                response.close()
        raise GitHubRepositoryError(message)

    logger.info(
        "GitHub issue comment created for %s/%s#%s with status %s",
        owner,
        repository,
        issue_number,
        response.status_code,
    )
    return response


def add_issue_labels(
    *,
    owner: str,
    repository: str,
    issue_number: int,
    token: str,
    labels: Iterable[str],
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    """Add labels to an issue and return the API response."""

    cleaned_labels = [str(label).strip() for label in labels if str(label).strip()]
    if not cleaned_labels:
        raise ValueError("Issue labels must not be empty")

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    url = f"{API_ROOT}/repos/{owner}/{repository}/issues/{issue_number}/labels"
    response = None
    try:
        response = requests.post(
            url,
            json={"labels": cleaned_labels},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    if not (200 <= response.status_code < 300):
        try:
            message = _extract_error_message(response)
        finally:
            with contextlib.suppress(Exception):
                response.close()
        raise GitHubRepositoryError(message)

    logger.info(
        "GitHub issue labels updated for %s/%s#%s with status %s",
        owner,
        repository,
        issue_number,
        response.status_code,
    )
    return response


def close_issue(
    *,
    owner: str,
    repository: str,
    issue_number: int,
    token: str,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response:
    """Close an issue and return the API response."""

    headers = build_headers(token, user_agent="arthexis-runtime-reporter")
    url = f"{API_ROOT}/repos/{owner}/{repository}/issues/{issue_number}"
    response = None
    try:
        response = requests.patch(
            url,
            json={"state": "closed"},
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as exc:  # pragma: no cover - network failure
        raise GitHubRepositoryError(str(exc)) from exc

    if not (200 <= response.status_code < 300):
        try:
            message = _extract_error_message(response)
        finally:
            with contextlib.suppress(Exception):
                response.close()
        raise GitHubRepositoryError(message)

    logger.info(
        "GitHub issue %s/%s#%s closed with status %s",
        owner,
        repository,
        issue_number,
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
