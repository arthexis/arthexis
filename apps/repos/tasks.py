from __future__ import annotations

import contextlib
import logging
import re
from typing import Any

from celery import shared_task
from django.apps import apps as django_apps

logger = logging.getLogger(__name__)

GITHUB_EXCEPTION_ISSUE_LABELS = ("automation", "bug", "priority: critical")
GITHUB_EXCEPTION_FINGERPRINT_PREFIX = "<!-- runtime-exception-fingerprint:"
GITHUB_EXCEPTION_FINGERPRINT_LOOKUP_LIMIT = 200
GITHUB_EXCEPTION_TRACEBACK_MAX_CHARS = 4000
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?P<key>\b(?:"
    r"authorization|api[_-]?key|cookie|password|passwd|"
    r"[a-z0-9_-]*(?:secret|token)[a-z0-9_-]*"
    r")\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;&]+)",
    re.IGNORECASE,
)
BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
UUID_PATH_TOKEN_RE = re.compile(
    r"(?<=/)[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?=[/?#]|$)",
    re.IGNORECASE,
)
SENSITIVE_PATH_TOKEN_RE = re.compile(
    r"(?<=/)(?:[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}|[A-Za-z0-9._~+=-]{16,})(?=[/?#]|$)",
    re.IGNORECASE,
)
INVITATION_PATH_RE = re.compile(
    r"(?P<prefix>/invitation/)[A-Za-z0-9._~+=-]+/[A-Za-z0-9._~+=-]+(?P<suffix>/?)"
)
AUTHORIZATION_HEADER_RE = re.compile(
    r"(?P<key>\bauthorization\b)(?P<sep>\s*[:=]\s*)(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)
COOKIE_HEADER_RE = re.compile(
    r"(?P<key>\bcookie\b)(?P<sep>\s*[:=]\s*)(?P<value>[^\r\n]+)",
    re.IGNORECASE,
)


def _redact_exception_path(value: object, *, broad_tokens: bool = True) -> str:
    path = str(value or "")
    path = INVITATION_PATH_RE.sub(
        lambda match: f"{match.group('prefix')}[REDACTED]/[REDACTED]{match.group('suffix')}",
        path,
    )
    token_re = SENSITIVE_PATH_TOKEN_RE if broad_tokens else UUID_PATH_TOKEN_RE
    return token_re.sub("[REDACTED]", path)


def _redact_exception_issue_text(
    value: object,
    *,
    broad_path_tokens: bool = False,
) -> str:
    """Return text safe enough for an automated GitHub issue."""

    text = _redact_exception_path(value, broad_tokens=broad_path_tokens)
    text = BEARER_TOKEN_RE.sub("Bearer [REDACTED]", text)
    text = AUTHORIZATION_HEADER_RE.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]",
        text,
    )
    text = COOKIE_HEADER_RE.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]",
        text,
    )
    return SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group('key')}{match.group('sep')}[REDACTED]",
        text,
    )


def _runtime_exception_fingerprint_marker(fingerprint: str) -> str:
    return f"{GITHUB_EXCEPTION_FINGERPRINT_PREFIX}{fingerprint} -->"


def _payload_text(payload: dict[str, Any], key: str, default: str = "unknown") -> str:
    value = payload.get(key)
    if value is None or value == "":
        return default
    if key == "path":
        return _redact_exception_issue_text(value, broad_path_tokens=True)
    return _redact_exception_issue_text(value)


def _payload_user_text(payload: dict[str, Any]) -> str:
    user = str(payload.get("user") or "").strip()
    if not user or user.lower() == "anonymous":
        return "anonymous"
    return "[REDACTED]"


def _top_stack_frame_text(payload: dict[str, Any]) -> str:
    top_frame = payload.get("top_stack_frame")
    if isinstance(top_frame, dict):
        filename = _redact_exception_issue_text(top_frame.get("filename") or "unknown")
        lineno = top_frame.get("lineno") or "?"
        name = _redact_exception_issue_text(top_frame.get("name") or "unknown")
        return f"{filename}:{lineno} in {name}"
    return _redact_exception_issue_text(top_frame or "unknown")


def _traceback_excerpt(payload: dict[str, Any]) -> str:
    traceback_text = _redact_exception_issue_text(payload.get("traceback") or "")
    if len(traceback_text) <= GITHUB_EXCEPTION_TRACEBACK_MAX_CHARS:
        return traceback_text
    return (
        traceback_text[:GITHUB_EXCEPTION_TRACEBACK_MAX_CHARS].rstrip()
        + "\n... [truncated]"
    )


def _runtime_exception_issue_title(payload: dict[str, Any]) -> str:
    exception_class = _payload_text(payload, "exception_class", "Runtime exception")
    path = _payload_text(payload, "path", "unknown path")
    title = f"Runtime exception: {exception_class} at {path}"
    return title[:180]


def _runtime_exception_issue_body(payload: dict[str, Any]) -> str:
    fingerprint = str(payload.get("fingerprint") or "").strip()
    traceback_excerpt = _traceback_excerpt(payload)
    return "\n".join(
        [
            "## Runtime exception report",
            "",
            f"- Exception: `{_payload_text(payload, 'exception_class')}`",
            f"- Path: `{_payload_text(payload, 'path')}`",
            f"- Method: `{_payload_text(payload, 'method')}`",
            f"- Active app: `{_payload_text(payload, 'active_app')}`",
            f"- User: `{_payload_user_text(payload)}`",
            f"- Top stack frame: `{_top_stack_frame_text(payload)}`",
            f"- Fingerprint: `{fingerprint}`",
            "",
            "## Redacted traceback excerpt",
            "",
            "````text",
            traceback_excerpt or "No traceback was provided.",
            "````",
            "",
            _runtime_exception_fingerprint_marker(fingerprint),
        ]
    )


def _runtime_exception_update_comment(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "## Repeated runtime exception",
            "",
            f"- Exception: `{_payload_text(payload, 'exception_class')}`",
            f"- Path: `{_payload_text(payload, 'path')}`",
            f"- Method: `{_payload_text(payload, 'method')}`",
            f"- Active app: `{_payload_text(payload, 'active_app')}`",
            f"- User: `{_payload_user_text(payload)}`",
            f"- Top stack frame: `{_top_stack_frame_text(payload)}`",
            f"- Fingerprint: `{str(payload.get('fingerprint') or '').strip()}`",
        ]
    )


def _issue_url_from_payload(issue: dict[str, Any]) -> str:
    return str(issue.get("html_url") or issue.get("url") or "").strip()


def _find_runtime_exception_issue(
    *,
    owner: str,
    repository: str,
    token: str,
    fingerprint: str,
) -> dict[str, Any] | None:
    from apps.repos.services import github as github_service

    marker = _runtime_exception_fingerprint_marker(fingerprint)
    remaining = GITHUB_EXCEPTION_FINGERPRINT_LOOKUP_LIMIT
    for state in ("open", "closed"):
        for issue in github_service.fetch_repository_issues(
            token=token,
            owner=owner,
            name=repository,
            state=state,
        ):
            if remaining <= 0:
                return None
            remaining -= 1
            if "pull_request" in issue:
                continue
            if marker in str(issue.get("body") or ""):
                return dict(issue)
    return None


def _exception_mentions_issue_labels(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    message_parts = [str(exc)]
    if response is not None:
        message_parts.append(str(getattr(response, "text", "") or ""))
    return "label" in " ".join(message_parts).lower()


def _create_runtime_exception_issue(issue_client, payload: dict[str, Any]):
    from apps.repos.services import github as github_service

    issue_kwargs = {
        "owner": issue_client.owner,
        "repository": issue_client.repository,
        "token": issue_client.token,
        "title": _runtime_exception_issue_title(payload),
        "body": _runtime_exception_issue_body(payload),
    }
    try:
        response = github_service.create_issue(
            **issue_kwargs,
            labels=GITHUB_EXCEPTION_ISSUE_LABELS,
        )
        if response is None:
            logger.warning(
                "Retrying runtime exception GitHub issue creation without labels "
                "after labeled create returned no response for %s",
                GITHUB_EXCEPTION_ISSUE_LABELS,
            )
            return github_service.create_issue(**issue_kwargs)
        return response
    except Exception as exc:
        if not _exception_mentions_issue_labels(exc):
            raise
        logger.warning(
            "Retrying runtime exception GitHub issue creation without labels: %s",
            exc,
        )
        return github_service.create_issue(**issue_kwargs)


@shared_task(name="apps.repos.tasks.report_exception_to_github")
def report_exception_to_github(payload: dict[str, Any]) -> str | None:
    """Create or update a GitHub issue for a queued request exception."""

    if not django_apps.is_installed("apps.repos"):
        logger.debug(
            "Runtime exception GitHub issue skipped; Repos app is not installed"
        )
        return None

    from apps.repos.services import github as github_service

    fingerprint = str(payload.get("fingerprint") or "").strip()
    if not fingerprint:
        logger.warning("Runtime exception GitHub issue skipped; fingerprint missing")
        return None

    try:
        issue_client = github_service.GitHubIssue.from_active_repository()
        existing_issue = _find_runtime_exception_issue(
            owner=issue_client.owner,
            repository=issue_client.repository,
            token=issue_client.token,
            fingerprint=fingerprint,
        )
        if existing_issue is not None:
            issue_number = int(existing_issue.get("number") or 0)
            if issue_number <= 0:
                logger.warning(
                    "Runtime exception GitHub issue update skipped for fingerprint %s; "
                    "existing issue number missing",
                    fingerprint,
                )
                return None

            if str(existing_issue.get("state") or "").lower() == "closed":
                response = github_service.reopen_issue(
                    owner=issue_client.owner,
                    repository=issue_client.repository,
                    issue_number=issue_number,
                    token=issue_client.token,
                )
                with contextlib.suppress(Exception):
                    response.close()

            response = github_service.create_issue_comment(
                issue_client.owner,
                issue_client.repository,
                issue_number=issue_number,
                token=issue_client.token,
                body=_runtime_exception_update_comment(payload),
            )
            with contextlib.suppress(Exception):
                response.close()

            issue_url = _issue_url_from_payload(existing_issue)
            logger.info(
                "Updated GitHub runtime exception issue %s for fingerprint %s",
                issue_url or f"#{issue_number}",
                fingerprint,
            )
            return issue_url

        response = _create_runtime_exception_issue(issue_client, payload)
        if response is None:
            return None
        try:
            try:
                response_payload = response.json()
            except ValueError as exc:
                raise github_service.GitHubRepositoryError(
                    "Unable to decode GitHub issue response"
                ) from exc
            if not isinstance(response_payload, dict):
                raise github_service.GitHubRepositoryError(
                    "Unable to decode GitHub issue response"
                )
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()

        issue_url = _issue_url_from_payload(response_payload)
        logger.info(
            "Created GitHub runtime exception issue %s for fingerprint %s",
            issue_url or "<unknown>",
            fingerprint,
        )
        return issue_url or None
    except github_service.GitHubRepositoryError as exc:
        logger.warning(
            "Runtime exception GitHub issue report failed for fingerprint %s: %s",
            fingerprint,
            exc,
        )
    except Exception:
        logger.exception(
            "Unexpected runtime exception GitHub issue report failure for fingerprint %s",
            fingerprint,
        )
    return None


@shared_task(name="apps.repos.tasks.monitor_github_readiness")
def monitor_github_readiness() -> dict[str, object]:
    """Poll configured GitHub readiness signals and maintain the operator queue."""

    from apps.repos import github_monitor

    try:
        return github_monitor.run_monitor_cycle(launch=True)
    except Exception as exc:
        github_monitor.notify_admins_of_failure(
            "Arthexis GitHub monitor failed",
            f"The GitHub monitor task failed before completing a cycle.\n\n{exc}",
        )
        raise


@shared_task(name="apps.repos.tasks.pull_upstream_repository_assignments")
def pull_upstream_repository_assignments() -> dict[str, object]:
    """Pull operator assignments from a configured upstream node when available."""

    from apps.repos.services import work_assignments

    return work_assignments.pull_assignments_from_upstream()


__all__ = [
    "monitor_github_readiness",
    "pull_upstream_repository_assignments",
    "report_exception_to_github",
]
