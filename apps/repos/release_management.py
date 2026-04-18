"""Release Management integration for GitHub repository workflows."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypedDict, TypeVar, cast

from typing_extensions import NotRequired

from apps.features.utils import is_suite_feature_enabled
from apps.repos.services import github as github_service

RELEASE_MANAGEMENT_FEATURE_SLUG = "release-management"
EXECUTION_MODE_KEY = "execution_mode"
EXECUTION_MODE_SUITE = "suite"
EXECUTION_MODE_BINARY = "binary"
MERGE_METHOD_MERGE = "merge"
MERGE_METHOD_SQUASH = "squash"
MERGE_METHOD_REBASE = "rebase"
MERGE_METHOD_CHOICES = (
    MERGE_METHOD_MERGE,
    MERGE_METHOD_SQUASH,
    MERGE_METHOD_REBASE,
)
COMMENT_KIND_ISSUE = "issue_comment"
COMMENT_KIND_REVIEW = "review_comment"
REACTION_EMOJI = {
    "eyes": "👀",
    "+1": "👍",
    "heart": "❤️",
    "rocket": "🚀",
    "hooray": "🎉",
    "laugh": "😄",
    "confused": "😕",
    "-1": "👎",
}


JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | dict[str, "JSONValue"] | list["JSONValue"]


class GitHubAuthorPayload(TypedDict, total=False):
    """Subset of GitHub author payload fields consumed by Arthexis."""

    login: str
    url: str


class GitHubIssuePayload(TypedDict, total=False):
    """Subset of issue fields consumed from suite API or gh output."""

    author: GitHubAuthorPayload
    html_url: str
    number: int
    state: str
    title: str
    user: GitHubAuthorPayload
    url: str


class GitHubIssueCreatePayload(TypedDict):
    """Subset of issue create response fields used for links."""

    html_url: NotRequired[str]
    url: NotRequired[str]


class GitHubPullRequestPayload(TypedDict, total=False):
    """Subset of pull-request fields consumed from suite API or gh output."""

    draft: bool
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


class GitHubReactionSummaryPayload(TypedDict, total=False):
    """Summary of reactions applied to a GitHub comment."""

    content: str
    count: int
    display: str
    emoji: str
    users: list[str]


class GitHubActivityPayload(TypedDict, total=False):
    """Normalized activity item for issue and pull-request observation."""

    author: GitHubAuthorPayload
    author_name: str
    body: str
    created_at: str
    html_url: str
    id: int
    kind: str
    kind_label: str
    line: int
    path: str
    reactions: list[GitHubReactionSummaryPayload]
    updated_at: str
    url: str


class ReleaseManagementError(RuntimeError):
    """Raised when Release Management operations fail."""


T = TypeVar("T")


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

    def _run_gh_api_items(self, endpoint: str) -> list[dict[str, JSONValue]]:
        rows = self._run_gh_json(["api", "--paginate", "--slurp", endpoint])
        return self._flatten_gh_api_items(rows)

    def _should_use_binary_first(self) -> bool:
        resolved = self.resolve_execution_mode(self._mode)
        return resolved == EXECUTION_MODE_BINARY

    def _can_use_suite_api(self) -> bool:
        return bool(self._resolve_token()) and self._feature_enabled()

    @staticmethod
    def _normalize_suite_error(exc: Exception) -> ReleaseManagementError:
        if isinstance(exc, ReleaseManagementError):
            return exc
        if isinstance(exc, github_service.GitHubRepositoryError):
            return ReleaseManagementError(str(exc))
        return ReleaseManagementError("GitHub operation failed")

    @classmethod
    def _run_suite_operation(cls, operation: Callable[[], T]) -> T:
        try:
            return operation()
        except (ReleaseManagementError, github_service.GitHubRepositoryError) as exc:
            raise cls._normalize_suite_error(exc) from exc

    @staticmethod
    def _normalize_merge_method(merge_method: str) -> str:
        normalized = str(merge_method or MERGE_METHOD_MERGE).strip().lower()
        if normalized not in MERGE_METHOD_CHOICES:
            allowed = ", ".join(MERGE_METHOD_CHOICES)
            raise ReleaseManagementError(
                f"Unsupported merge method '{merge_method}'. Expected one of: {allowed}"
            )
        return normalized

    def list_issues(self, repository: RepositoryRef, *, state: str = "open") -> list[GitHubIssuePayload]:
        """List issues using suite API first unless binary mode is selected."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            issues = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_repository_issues(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        state=state,
                    )
                )
            )
            return [
                self._coerce_issue_payload(cast(dict[str, JSONValue], item))
                for item in issues
                if "pull_request" not in item
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
            response = self._run_suite_operation(
                lambda: github_service.create_issue(
                    repository.owner,
                    repository.name,
                    token=token,
                    title=title,
                    body=body,
                )
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

    def comment_issue(
        self,
        repository: RepositoryRef,
        *,
        number: int,
        body: str,
    ) -> None:
        """Add a comment to an issue."""

        cleaned_body = str(body or "").strip()
        if not cleaned_body:
            raise ReleaseManagementError("Issue comment body must not be empty")

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            self._run_suite_operation(
                lambda: github_service.create_issue_comment(
                    repository.owner,
                    repository.name,
                    issue_number=number,
                    token=token,
                    body=cleaned_body,
                )
            )
            return

        self._run_gh(
            [
                "issue",
                "comment",
                str(number),
                "--repo",
                repository.slug,
                "--body",
                cleaned_body,
            ]
        )

    def close_issue(self, repository: RepositoryRef, *, number: int) -> None:
        """Close an issue."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            self._run_suite_operation(
                lambda: github_service.close_issue(
                    repository.owner,
                    repository.name,
                    issue_number=number,
                    token=token,
                )
            )
            return

        self._run_gh(["issue", "close", str(number), "--repo", repository.slug])

    def list_issue_activity(
        self,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> list[GitHubActivityPayload]:
        """Return live issue comments with reaction summaries."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            comments = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_issue_comments(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        issue_number=number,
                    )
                )
            )
            return [
                self._coerce_activity_payload(
                    cast(dict[str, JSONValue], comment),
                    kind=COMMENT_KIND_ISSUE,
                    reactions=self._summarize_reactions(
                        list(
                            github_service.fetch_issue_comment_reactions(
                                token=token,
                                owner=repository.owner,
                                name=repository.name,
                                comment_id=int(comment.get("id") or 0),
                            )
                        )
                    ),
                )
                for comment in comments
                if isinstance(comment.get("id"), int)
            ]

        comments = self._run_gh_api_items(
            f"repos/{repository.slug}/issues/{number}/comments?per_page=100"
        )
        return [
            self._coerce_activity_payload(
                comment,
                kind=COMMENT_KIND_ISSUE,
                reactions=self._summarize_reactions(
                    self._run_gh_api_items(
                        f"repos/{repository.slug}/issues/comments/{comment_id}/reactions?per_page=100"
                    )
                ),
            )
            for comment in comments
            if isinstance((comment_id := comment.get("id")), int)
        ]

    def list_pull_requests(
        self, repository: RepositoryRef, *, state: str = "open"
    ) -> list[GitHubPullRequestPayload]:
        """List pull requests through suite API with gh fallback."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            pull_requests = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_repository_pull_requests(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        state=state,
                    )
                )
            )
            return [
                self._coerce_pull_request_payload(cast(dict[str, JSONValue], item))
                for item in pull_requests
            ]

        query = "number,title,state,url,isDraft"
        rows = self._run_gh_json(
            ["pr", "list", "--repo", repository.slug, "--state", state, "--json", query]
        )
        return cast(list[GitHubPullRequestPayload], rows) if isinstance(rows, list) else []

    def list_pull_request_activity(
        self,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> list[GitHubActivityPayload]:
        """Return live pull-request conversation and inline review comments."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            issue_comments = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_issue_comments(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        issue_number=number,
                    )
                )
            )
            review_comments = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_pull_request_review_comments(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        pull_number=number,
                    )
                )
            )
            return self._sort_activity(
                [
                    *[
                        self._coerce_activity_payload(
                            cast(dict[str, JSONValue], comment),
                            kind=COMMENT_KIND_ISSUE,
                            reactions=self._summarize_reactions(
                                list(
                                    github_service.fetch_issue_comment_reactions(
                                        token=token,
                                        owner=repository.owner,
                                        name=repository.name,
                                        comment_id=int(comment.get("id") or 0),
                                    )
                                )
                            ),
                        )
                        for comment in issue_comments
                        if isinstance(comment.get("id"), int)
                    ],
                    *[
                        self._coerce_activity_payload(
                            cast(dict[str, JSONValue], comment),
                            kind=COMMENT_KIND_REVIEW,
                            reactions=self._summarize_reactions(
                                list(
                                    github_service.fetch_pull_request_review_comment_reactions(
                                        token=token,
                                        owner=repository.owner,
                                        name=repository.name,
                                        comment_id=int(comment.get("id") or 0),
                                    )
                                )
                            ),
                        )
                        for comment in review_comments
                        if isinstance(comment.get("id"), int)
                    ],
                ]
            )

        issue_comments = self._run_gh_api_items(
            f"repos/{repository.slug}/issues/{number}/comments?per_page=100"
        )
        review_comments = self._run_gh_api_items(
            f"repos/{repository.slug}/pulls/{number}/comments?per_page=100"
        )
        return self._sort_activity(
            [
                *[
                    self._coerce_activity_payload(
                        comment,
                        kind=COMMENT_KIND_ISSUE,
                        reactions=self._summarize_reactions(
                            self._run_gh_api_items(
                                f"repos/{repository.slug}/issues/comments/{comment_id}/reactions?per_page=100"
                            )
                        ),
                    )
                    for comment in issue_comments
                    if isinstance((comment_id := comment.get("id")), int)
                ],
                *[
                    self._coerce_activity_payload(
                        comment,
                        kind=COMMENT_KIND_REVIEW,
                        reactions=self._summarize_reactions(
                            self._run_gh_api_items(
                                f"repos/{repository.slug}/pulls/comments/{comment_id}/reactions?per_page=100"
                            )
                        ),
                    )
                    for comment in review_comments
                    if isinstance((comment_id := comment.get("id")), int)
                ],
            ]
        )

    def comment_pull_request(
        self,
        repository: RepositoryRef,
        *,
        number: int,
        body: str,
    ) -> None:
        """Add a comment to a pull request."""

        cleaned_body = str(body or "").strip()
        if not cleaned_body:
            raise ReleaseManagementError("Pull request comment body must not be empty")

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            self._run_suite_operation(
                lambda: github_service.create_pull_request_comment(
                    repository.owner,
                    repository.name,
                    pull_number=number,
                    token=token,
                    body=cleaned_body,
                )
            )
            return

        self._run_gh(
            [
                "pr",
                "comment",
                str(number),
                "--repo",
                repository.slug,
                "--body",
                cleaned_body,
            ]
        )

    def mark_pull_request_ready(
        self,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> None:
        """Move a draft pull request to ready-for-review."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            self._run_suite_operation(
                lambda: github_service.mark_pull_request_ready(
                    repository.owner,
                    repository.name,
                    pull_number=number,
                    token=token,
                )
            )
            return

        self._run_gh(["pr", "ready", str(number), "--repo", repository.slug])

    def merge_pull_request(
        self,
        repository: RepositoryRef,
        *,
        number: int,
        merge_method: str = MERGE_METHOD_MERGE,
    ) -> None:
        """Merge a pull request using the selected merge method."""

        normalized_method = self._normalize_merge_method(merge_method)
        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            self._run_suite_operation(
                lambda: github_service.merge_pull_request(
                    repository.owner,
                    repository.name,
                    pull_number=number,
                    token=token,
                    merge_method=normalized_method,
                )
            )
            return

        self._run_gh(
            [
                "pr",
                "merge",
                str(number),
                "--repo",
                repository.slug,
                f"--{normalized_method}",
            ]
        )

    def get_issue(
        self,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> GitHubIssuePayload | None:
        """Return issue metadata for a specific issue number."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            issues = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_repository_issues(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        state="all",
                    )
                )
            )
            row = next(
                (
                    item
                    for item in issues
                    if item.get("number") == number and "pull_request" not in item
                ),
                None,
            )
            if isinstance(row, dict):
                return self._coerce_issue_payload(cast(dict[str, JSONValue], row))
            return None

        query = "number,title,state,url,author"
        rows = self._run_gh_json(
            ["issue", "view", str(number), "--repo", repository.slug, "--json", query]
        )
        if isinstance(rows, dict):
            return cast(GitHubIssuePayload, rows)
        return None

    def get_pull_request(
        self,
        repository: RepositoryRef,
        *,
        number: int,
    ) -> GitHubPullRequestPayload | None:
        """Return pull-request metadata for a specific pull request number."""

        token = self._resolve_token()
        if not self._should_use_binary_first() and token and self._can_use_suite_api():
            pull_requests = self._run_suite_operation(
                lambda: list(
                    github_service.fetch_repository_pull_requests(
                        token=token,
                        owner=repository.owner,
                        name=repository.name,
                        state="all",
                    )
                )
            )
            row = next((item for item in pull_requests if item.get("number") == number), None)
            if isinstance(row, dict):
                return self._coerce_pull_request_payload(cast(dict[str, JSONValue], row))
            return None

        rows = self._run_gh_json(
            [
                "pr",
                "view",
                str(number),
                "--repo",
                repository.slug,
                "--json",
                "number,title,state,url,isDraft",
            ]
        )
        if isinstance(rows, dict):
            return cast(GitHubPullRequestPayload, rows)
        return None

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

    @staticmethod
    def _coerce_issue_payload(item: dict[str, JSONValue]) -> GitHubIssuePayload:
        payload: GitHubIssuePayload = cast(GitHubIssuePayload, item)
        user = item.get("user")
        if isinstance(user, dict) and "author" not in payload:
            payload["author"] = cast(GitHubAuthorPayload, user)
        html_url = item.get("html_url")
        if isinstance(html_url, str) and "url" not in payload:
            payload["url"] = html_url
        return payload

    @staticmethod
    def _coerce_pull_request_payload(item: dict[str, JSONValue]) -> GitHubPullRequestPayload:
        payload: GitHubPullRequestPayload = cast(GitHubPullRequestPayload, item)
        draft = item.get("draft")
        if isinstance(draft, bool) and "isDraft" not in payload:
            payload["isDraft"] = draft
        return payload

    @staticmethod
    def _flatten_gh_api_items(value: JSONValue) -> list[dict[str, JSONValue]]:
        items: list[dict[str, JSONValue]] = []
        if not isinstance(value, list):
            return items
        for entry in value:
            if isinstance(entry, dict):
                items.append(cast(dict[str, JSONValue], entry))
                continue
            if not isinstance(entry, list):
                continue
            for nested in entry:
                if isinstance(nested, dict):
                    items.append(cast(dict[str, JSONValue], nested))
        return items

    @staticmethod
    def _summarize_reactions(
        reactions: list[Mapping[str, object]],
    ) -> list[GitHubReactionSummaryPayload]:
        grouped: dict[str, list[str]] = {}
        for reaction in reactions:
            content = str(reaction.get("content") or "").strip()
            if not content:
                continue
            user = reaction.get("user")
            login = ""
            if isinstance(user, dict):
                login = str(user.get("login") or "").strip()
            grouped.setdefault(content, [])
            if login and login not in grouped[content]:
                grouped[content].append(login)

        summary: list[GitHubReactionSummaryPayload] = []
        seen = set()
        for content in (*REACTION_EMOJI.keys(), *grouped.keys()):
            if content in seen or content not in grouped:
                continue
            seen.add(content)
            users = grouped[content]
            emoji = REACTION_EMOJI.get(content, content)
            display = f"{emoji} {', '.join(users)}" if users else f"{emoji} x{len(users)}"
            summary.append(
                {
                    "content": content,
                    "count": len(users),
                    "display": display,
                    "emoji": emoji,
                    "users": users,
                }
            )
        return summary

    @staticmethod
    def _sort_activity(
        activity: list[GitHubActivityPayload],
    ) -> list[GitHubActivityPayload]:
        return sorted(
            activity,
            key=lambda item: (
                str(item.get("created_at") or ""),
                str(item.get("kind") or ""),
                int(item.get("id") or 0),
            ),
        )

    @staticmethod
    def _coerce_activity_payload(
        item: dict[str, JSONValue],
        *,
        kind: str,
        reactions: list[GitHubReactionSummaryPayload],
    ) -> GitHubActivityPayload:
        user = item.get("user")
        author = cast(GitHubAuthorPayload, user) if isinstance(user, dict) else {}
        html_url = item.get("html_url")
        url = item.get("url")
        line = item.get("line")
        payload: GitHubActivityPayload = {
            "author": author,
            "author_name": str(author.get("login") or ""),
            "body": str(item.get("body") or ""),
            "created_at": str(item.get("created_at") or ""),
            "html_url": str(html_url or url or ""),
            "id": int(item.get("id") or 0),
            "kind": kind,
            "kind_label": "Review comment"
            if kind == COMMENT_KIND_REVIEW
            else "Issue comment",
            "reactions": reactions,
            "updated_at": str(item.get("updated_at") or ""),
            "url": str(url or html_url or ""),
        }
        path = item.get("path")
        if isinstance(path, str) and path.strip():
            payload["path"] = path
        if isinstance(line, int):
            payload["line"] = line
        return payload


__all__ = [
    "COMMENT_KIND_ISSUE",
    "COMMENT_KIND_REVIEW",
    "EXECUTION_MODE_BINARY",
    "EXECUTION_MODE_KEY",
    "EXECUTION_MODE_SUITE",
    "GitHubActivityPayload",
    "MERGE_METHOD_CHOICES",
    "MERGE_METHOD_MERGE",
    "MERGE_METHOD_REBASE",
    "MERGE_METHOD_SQUASH",
    "REACTION_EMOJI",
    "RELEASE_MANAGEMENT_FEATURE_SLUG",
    "ReleaseManagementClient",
    "ReleaseManagementError",
    "RepositoryRef",
]
