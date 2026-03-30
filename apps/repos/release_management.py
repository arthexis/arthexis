"""Release Management integration for GitHub repository workflows."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import TypedDict, cast

from typing_extensions import NotRequired

from apps.features.utils import is_suite_feature_enabled
from apps.repos.services import github as github_service

RELEASE_MANAGEMENT_FEATURE_SLUG = "release-management"
EXECUTION_MODE_KEY = "execution_mode"
EXECUTION_MODE_SUITE = "suite"
EXECUTION_MODE_BINARY = "binary"


JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | dict[str, "JSONValue"] | list["JSONValue"]


class GitHubAuthorPayload(TypedDict, total=False):
    """Subset of GitHub author payload fields consumed by Arthexis."""

    login: str
    url: str


class GitHubIssuePayload(TypedDict, total=False):
    """Subset of issue fields consumed from suite API or gh output."""

    author: GitHubAuthorPayload
    number: int
    pull_request: dict[str, JSONValue]
    state: str
    title: str
    url: str


class GitHubIssueCreatePayload(TypedDict):
    """Subset of issue create response fields used for links."""

    html_url: NotRequired[str]
    url: NotRequired[str]


class GitHubPullRequestPayload(TypedDict, total=False):
    """Subset of pull-request fields consumed from suite API or gh output."""

    isDraft: bool
    number: int
    state: str
    title: str
    url: str


class GitHubReleasePayload(TypedDict, total=False):
    """Subset of release fields consumed from gh output."""

    isDraft: bool
    isLatest: bool
    name: str
    publishedAt: str
    tagName: str
    url: str


class ReleaseManagementError(RuntimeError):
    """Raised when Release Management operations fail."""


@dataclass(slots=True, frozen=True)
class RepositoryRef:
    """Repository identity used by Release Management operations."""

    owner: str
    name: str

    @property
    def slug(self) -> str:
        """Return owner/name slug for command and API operations."""

        return f"{self.owner}/{self.name}".strip("/")


class ReleaseManagementClient:
    """Coordinate suite/API and GitHub CLI repository operations."""

    def __init__(self, *, token: str | None = None, mode: str | None = None) -> None:
        """Create client with optional explicit auth token and execution mode."""

        self._token = (token or "").strip() or None
        self._mode = self._normalize_mode(mode)

    @staticmethod
    def _normalize_mode(mode: str | None) -> str | None:
        value = (mode or "").strip().lower()
        if value in {EXECUTION_MODE_BINARY, EXECUTION_MODE_SUITE}:
            return value
        return None

    @staticmethod
    def _feature_enabled() -> bool:
        return is_suite_feature_enabled(RELEASE_MANAGEMENT_FEATURE_SLUG, default=True)

    @staticmethod
    def _feature_mode() -> str:
        from apps.features.parameters import get_feature_parameter

        configured = get_feature_parameter(
            RELEASE_MANAGEMENT_FEATURE_SLUG,
            EXECUTION_MODE_KEY,
            fallback=EXECUTION_MODE_SUITE,
        )
        normalized = str(configured).strip().lower()
        if normalized in {EXECUTION_MODE_SUITE, EXECUTION_MODE_BINARY}:
            return normalized
        return EXECUTION_MODE_SUITE

    @classmethod
    def resolve_execution_mode(cls, explicit_mode: str | None = None) -> str:
        """Resolve operation mode from explicit value, then feature metadata."""

        if explicit_mode:
            normalized = str(explicit_mode).strip().lower()
            if normalized in {EXECUTION_MODE_SUITE, EXECUTION_MODE_BINARY}:
                return normalized
        return cls._feature_mode()

    def _resolve_token(self) -> str | None:
        if self._token:
            return self._token

        try:
            token = github_service.get_github_issue_token()
        except RuntimeError:
            token = None
        if token:
            return token

        env_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        cleaned = env_token.strip() if isinstance(env_token, str) else ""
        return cleaned or None

    @staticmethod
    def _ensure_gh_available() -> None:
        try:
            subprocess.run(["gh", "--version"], check=True, capture_output=True, text=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise ReleaseManagementError("GitHub CLI is unavailable; install/authenticate gh first") from exc

    def _run_gh_json(self, args: list[str]) -> JSONValue:
        self._ensure_gh_available()
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "gh command failed"
            raise ReleaseManagementError(message)
        output = completed.stdout.strip()
        if not output:
            return []
        return cast(JSONValue, json.loads(output))

    def _run_gh(self, args: list[str]) -> str:
        self._ensure_gh_available()
        completed = subprocess.run(["gh", *args], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "gh command failed"
            raise ReleaseManagementError(message)
        return completed.stdout.strip()

    def _should_use_binary_first(self) -> bool:
        resolved = self.resolve_execution_mode(self._mode)
        return resolved == EXECUTION_MODE_BINARY

    def _can_use_suite_api(self) -> bool:
        return bool(self._resolve_token()) and self._feature_enabled()

    def list_issues(self, repository: RepositoryRef, *, state: str = "open") -> list[GitHubIssuePayload]:
        """List issues using suite API first unless binary mode is selected."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            issues = list(
                github_service.fetch_repository_issues(
                    token=token,
                    owner=repository.owner,
                    name=repository.name,
                    state=state,
                )
            )
            return [
                cast(GitHubIssuePayload, item) for item in issues if "pull_request" not in item
            ]

        query = "number,title,state,url,author"
        rows = self._run_gh_json(
            [
                "issue",
                "list",
                "--repo",
                repository.slug,
                "--state",
                state,
                "--json",
                query,
            ]
        )
        return cast(list[GitHubIssuePayload], rows) if isinstance(rows, list) else []

    def create_issue(self, repository: RepositoryRef, *, title: str, body: str) -> str:
        """Create an issue through suite API with binary fallback."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            response = github_service.create_issue(
                repository.owner,
                repository.name,
                token=token,
                title=title,
                body=body,
            )
            if response is not None:
                raw_payload = response.json()
                if isinstance(raw_payload, dict):
                    payload = cast(GitHubIssueCreatePayload, raw_payload)
                    return str(payload.get("html_url") or payload.get("url") or "")

        return self._run_gh(
            [
                "issue",
                "create",
                "--repo",
                repository.slug,
                "--title",
                title,
                "--body",
                body,
            ]
        )

    def list_pull_requests(
        self, repository: RepositoryRef, *, state: str = "open"
    ) -> list[GitHubPullRequestPayload]:
        """List pull requests through suite API with gh fallback."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            pull_requests = list(
                github_service.fetch_repository_pull_requests(
                    token=token,
                    owner=repository.owner,
                    name=repository.name,
                    state=state,
                )
            )
            return [cast(GitHubPullRequestPayload, item) for item in pull_requests]

        query = "number,title,state,url,isDraft"
        rows = self._run_gh_json(
            ["pr", "list", "--repo", repository.slug, "--state", state, "--json", query]
        )
        return cast(list[GitHubPullRequestPayload], rows) if isinstance(rows, list) else []

    def create_release(self, repository: RepositoryRef, *, tag: str, title: str, notes: str) -> str:
        """Create a GitHub release via gh CLI."""

        return self._run_gh(
            [
                "release",
                "create",
                "--repo",
                repository.slug,
                "--title",
                title,
                "--notes",
                notes,
                "--",
                tag,
            ]
        )

    def list_releases(self, repository: RepositoryRef, *, limit: int = 20) -> list[GitHubReleasePayload]:
        """List releases via gh CLI JSON output."""

        rows = self._run_gh_json(
            [
                "release",
                "list",
                "--repo",
                repository.slug,
                "--limit",
                str(limit),
                "--json",
                "tagName,name,isDraft,isLatest,publishedAt,url",
            ]
        )
        return cast(list[GitHubReleasePayload], rows) if isinstance(rows, list) else []


__all__ = [
    "EXECUTION_MODE_BINARY",
    "EXECUTION_MODE_SUITE",
    "RELEASE_MANAGEMENT_FEATURE_SLUG",
    "ReleaseManagementClient",
    "ReleaseManagementError",
    "RepositoryRef",
]
