"""Typed payload structures shared by PR oversight helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypedDict

JSONValue = dict[str, Any] | list[Any] | str | int | float | bool | None


class PullRequestOverseeError(RuntimeError):
    """Raised when PR oversight cannot complete deterministically."""


@dataclass(slots=True)
class CommandResult:
    """Subprocess result captured by the command runner."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class AuthorPayload(TypedDict, total=False):
    login: str


class CheckPayload(TypedDict, total=False):
    app: dict[str, Any]
    completedAt: str
    conclusion: str
    context: str
    createdAt: str
    detailsUrl: str
    link: str
    name: str
    startedAt: str
    state: str
    status: str
    targetUrl: str
    updatedAt: str
    workflow: str
    workflowName: str


class CheckEntry(TypedDict):
    name: str
    status: str
    state: str
    conclusion: str
    value: str
    detailsUrl: str


class CheckRollupState(TypedDict):
    advisory: list[CheckEntry]
    failing: list[CheckEntry]
    pending: list[CheckEntry]
    passing: list[CheckEntry]
    superseded: list[CheckEntry]


class PullRequestPayload(TypedDict, total=False):
    author: AuthorPayload | str
    baseRefName: str
    baseRefOid: str
    body: str
    files: list[dict[str, Any]]
    headRefName: str
    headRefOid: str
    isDraft: bool
    mergeable: str
    mergeStateStatus: str
    number: int
    reviewDecision: str
    state: str
    statusCheckRollup: list[CheckPayload]
    title: str
    unresolvedReviewThreadCount: int
    updatedAt: str
    url: str


class ReadinessGateResult(TypedDict):
    number: object
    title: object
    author: str
    url: object
    headRefName: object
    headRefOid: object
    baseRefName: object
    baseRefOid: object
    ready: bool
    blockers: list[str]
    warnings: list[str]
    mergeStateStatus: object
    mergeable: object
    reviewDecision: object
    checks: CheckRollupState
    unresolvedReviewThreadCount: int
    updatedAt: object


class ReviewCommentPayload(TypedDict, total=False):
    author: str
    body: str
    createdAt: str
    line: int | None
    path: str
    url: str


class ReviewThreadPayload(TypedDict, total=False):
    comments: list[ReviewCommentPayload]
    isOutdated: bool
    isResolved: bool
    line: int | None
    path: str


def _json_loads(raw_value: str) -> JSONValue:
    if not raw_value.strip():
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise PullRequestOverseeError(
            f"Command did not return valid JSON: {exc}"
        ) from exc


def _coerce_mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _coerce_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []
