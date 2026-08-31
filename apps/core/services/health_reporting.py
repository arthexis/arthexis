"""GitHub issue reporting for management health-check targets."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import shlex
import socket
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.apps import apps as django_apps
from django.conf import settings

if TYPE_CHECKING:
    from apps.core.services.health import HealthCheckDefinition


logger = logging.getLogger(__name__)

HEALTH_ISSUE_LABELS = ("automation", "bug", "priority: high")
HEALTH_REPORT_LOCK_DIRNAME = "github-health"
_SENSITIVE_ASSIGNMENT_KEYS = (
    "password",
    "api_key",
    "api-key",
    "apikey",
    "rfid",
    "rfid_value",
    "rfid-value",
    "secret",
    "token",
)
_IDENTITY_SELECTOR_FLAGS_WITH_VALUES = {"--group", "--target"}
_IDENTITY_SELECTOR_FLAG_PREFIXES = ("--group=", "--target=")
_IDENTITY_SELECTOR_BOOL_FLAGS = {"--all", "--report-github"}
_SENSITIVE_COMMAND_FLAGS_WITH_VALUES = {"--rfid-value"}
_SENSITIVE_COMMAND_FLAG_PREFIXES = ("--rfid-value=",)


def health_check_fingerprint(
    definition: HealthCheckDefinition,
    *,
    command_text: str,
) -> str:
    """Return the stable issue fingerprint for one health invocation."""

    return hashlib.sha256(
        (
            f"health-check|{definition.target}|{_node_identity_key()}|"
            f"{_command_identity_digest(command_text)}"
        ).encode()
    ).hexdigest()


def health_check_fingerprint_marker(fingerprint: str) -> str:
    return f"<!-- health-check-fingerprint:{fingerprint} -->"


def _reporting_enabled() -> bool:
    if not django_apps.is_installed("apps.repos"):
        return False

    from apps.repos.issue_reporting import is_github_issue_reporting_enabled

    return is_github_issue_reporting_enabled()


def _lock_dir() -> Path:
    return Path(settings.BASE_DIR) / ".locks" / HEALTH_REPORT_LOCK_DIRNAME


def _lock_path(fingerprint: str) -> Path:
    return _lock_dir() / fingerprint


def _try_reserve_failure_report(fingerprint: str) -> bool:
    cooldown = float(getattr(settings, "GITHUB_ISSUE_REPORTING_COOLDOWN", 3600))
    path = _lock_path(fingerprint)
    for _attempt in range(2):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8") as handle:
                handle.write(str(time.time()))
            return True
        except FileExistsError:
            if _failure_report_is_recent(path, cooldown):
                return False
            with contextlib.suppress(OSError):
                path.unlink()
        except OSError as exc:
            logger.warning(
                "Unable to reserve GitHub health issue report lock %s: %s",
                path,
                exc,
            )
            return True
    return False


def _failure_report_is_recent(path: Path, cooldown: float) -> bool:
    with contextlib.suppress(OSError):
        return time.time() - path.stat().st_mtime < cooldown
    return False


def _touch_failure_report(fingerprint: str) -> None:
    path = _lock_path(fingerprint)
    with contextlib.suppress(OSError):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")


def _clear_failure_report(fingerprint: str) -> None:
    with contextlib.suppress(OSError):
        _lock_path(fingerprint).unlink()


def _redact(value: object) -> str:
    text = str(value or "")
    return _redact_sensitive_command_flags(
        _redact_secret_assignments(_redact_authorization_headers(text))
    )


def _redact_sensitive_command_flags(text: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(text):
        flag_match = _sensitive_command_flag_match(text, index)
        if flag_match is None:
            parts.append(text[index])
            index += 1
            continue

        flag_end, value_start = flag_match
        parts.append(text[index:flag_end])
        value_end = _redacted_command_flag_value_end(text, value_start)
        parts.append("[REDACTED]")
        index = value_end
    return "".join(parts)


def _sensitive_command_flag_match(text: str, index: int) -> tuple[int, int] | None:
    if index > 0 and not text[index - 1].isspace():
        return None
    for prefix in _SENSITIVE_COMMAND_FLAG_PREFIXES:
        if text.startswith(prefix, index) and index + len(prefix) < len(text):
            return index + len(prefix), index + len(prefix)
    for flag in _SENSITIVE_COMMAND_FLAGS_WITH_VALUES:
        if not text.startswith(flag, index):
            continue
        flag_end = index + len(flag)
        if flag_end < len(text) and not text[flag_end].isspace():
            continue
        value_start = flag_end
        while value_start < len(text) and text[value_start] in " \t":
            value_start += 1
        if value_start < len(text) and text[value_start] not in "\r\n":
            return value_start, value_start
    return None


def _redacted_command_flag_value_end(text: str, value_start: int) -> int:
    if text[value_start] in ("'", '"'):
        closing_quote = _find_closing_quote(
            text,
            value_start + 1,
            text[value_start],
        )
        if closing_quote is not None:
            return closing_quote + 1
        return len(text)
    index = value_start
    while index < len(text) and not text[index].isspace():
        index += 1
    return index


def _redact_authorization_headers(text: str) -> str:
    return "".join(
        _redact_authorization_header_line(line)
        for line in text.splitlines(keepends=True)
    )


def _redact_authorization_header_line(line: str) -> str:
    body, line_ending = _split_line_ending(line)
    stripped = body.lstrip()
    leading = body[: len(body) - len(stripped)]
    key, separator, value = stripped.partition(":")
    if separator != ":" or key.strip().casefold() != "authorization":
        return line
    spacing = value[: len(value) - len(value.lstrip())]
    return f"{leading}{key}:{spacing}[REDACTED]{line_ending}"


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1]
    return line, ""


def _redact_secret_assignments(text: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(text):
        assignment = _assignment_match(text, index)
        if assignment is None:
            parts.append(text[index])
            index += 1
            continue

        key_end, value_start = assignment
        parts.append(f"{text[index:key_end]}=")
        value_end, replacement = _redacted_assignment_value(text, index, value_start)
        parts.append(replacement)
        index = value_end
    return "".join(parts)


def _assignment_match(text: str, index: int) -> tuple[int, int] | None:
    if index > 0 and (text[index - 1].isalnum() or text[index - 1] == "_"):
        return None
    folded_text = text.casefold()
    for key in _SENSITIVE_ASSIGNMENT_KEYS:
        key_end = index + len(key)
        if not folded_text.startswith(key, index):
            continue
        equals_index = key_end
        while equals_index < len(text) and text[equals_index] in " \t":
            equals_index += 1
        if equals_index >= len(text) or text[equals_index] != "=":
            continue
        value_start = equals_index + 1
        while value_start < len(text) and text[value_start] in " \t":
            value_start += 1
        if value_start < len(text) and text[value_start] not in "\r\n":
            return key_end, value_start
    return None


def _redacted_assignment_value(
    text: str,
    key_start: int,
    value_start: int,
) -> tuple[int, str]:
    if text[value_start] in ("'", '"'):
        quote = text[value_start]
        closing_quote = _find_closing_quote(text, value_start + 1, quote)
        if closing_quote is None:
            return len(text), f"{quote}[REDACTED]"
        return closing_quote + 1, f"{quote}[REDACTED]{quote}"

    active_quote = _active_quote_at(text, key_start)
    if active_quote is not None:
        closing_quote = _find_closing_quote(text, value_start, active_quote)
        if closing_quote is not None:
            return closing_quote, "[REDACTED]"

    index = value_start
    while index < len(text) and not text[index].isspace():
        index += 1
    return index, "[REDACTED]"


def _active_quote_at(text: str, index: int) -> str | None:
    quote: str | None = None
    escaped = False
    line_start = max(text.rfind("\n", 0, index), text.rfind("\r", 0, index)) + 1
    for char in text[line_start:index]:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is not None:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
    return quote


def _find_closing_quote(text: str, start: int, quote: str) -> int | None:
    escaped = False
    index = start
    while index < len(text):
        char = text[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == quote:
            escaped_fragment_end = _shell_escaped_quote_fragment_end(
                text,
                index,
                quote,
            )
            if escaped_fragment_end is not None:
                index = escaped_fragment_end
                continue
            return index
        index += 1
    return None


def _shell_escaped_quote_fragment_end(
    text: str,
    index: int,
    quote: str,
) -> int | None:
    if quote == "'" and text.startswith("'\"'\"'", index):
        return index + len("'\"'\"'")
    return None


def _node_identity_lines() -> list[str]:
    return [
        f"- Node role: `{getattr(settings, 'NODE_ROLE', '') or 'unknown'}`",
        f"- Hostname: `{socket.gethostname()}`",
    ]


def _node_identity_key() -> str:
    node_role = str(getattr(settings, "NODE_ROLE", "") or "unknown").strip()
    hostname = socket.gethostname().strip()
    return f"{node_role or 'unknown'}|{hostname or 'unknown'}".casefold()


def _command_identity_digest(command_text: str) -> str:
    return hashlib.sha256(
        "\0".join(_command_identity_tokens(command_text)).encode()
    ).hexdigest()


def _command_identity_tokens(command_text: str) -> tuple[str, ...]:
    try:
        tokens = shlex.split(command_text)
    except ValueError:
        tokens = command_text.split()

    if tokens[:2] == ["manage.py", "health"]:
        tokens = tokens[2:]

    identity_tokens: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _IDENTITY_SELECTOR_FLAGS_WITH_VALUES:
            index += 2
            continue
        if token.startswith(_IDENTITY_SELECTOR_FLAG_PREFIXES):
            index += 1
            continue
        if token in _IDENTITY_SELECTOR_BOOL_FLAGS:
            index += 1
            continue
        if token in _SENSITIVE_COMMAND_FLAGS_WITH_VALUES:
            identity_tokens.append(token)
            index += 2
            continue
        if token.startswith(_SENSITIVE_COMMAND_FLAG_PREFIXES):
            identity_tokens.append(token.split("=", 1)[0])
            index += 1
            continue
        identity_tokens.append(token)
        index += 1
    return tuple(identity_tokens)


def _issue_url(issue: dict[str, Any]) -> str | None:
    return str(issue.get("html_url") or "").strip() or None


def _find_health_issue(
    *,
    github_service,
    issue_client,
    fingerprint: str,
    states: tuple[str, ...] = ("open", "closed"),
) -> dict[str, Any] | None:
    marker = health_check_fingerprint_marker(fingerprint)
    for state in states:
        for issue in github_service.fetch_repository_issues(
            token=issue_client.token,
            owner=issue_client.owner,
            name=issue_client.repository,
            state=state,
        ):
            if "pull_request" in issue:
                continue
            if marker in str(issue.get("body") or ""):
                return dict(issue)
    return None


def _label_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    message_parts = [str(exc)]
    if response is not None:
        message_parts.append(str(getattr(response, "text", "") or ""))
    return "label" in " ".join(message_parts).lower()


def _failure_issue_title(definition: HealthCheckDefinition) -> str:
    return f"Health check failed: {definition.target}"


def _failure_issue_body(
    *,
    definition: HealthCheckDefinition,
    fingerprint: str,
    failure_message: str,
    command_text: str,
) -> str:
    return "\n".join(
        [
            health_check_fingerprint_marker(fingerprint),
            "",
            "A `manage.py health` target failed.",
            "",
            f"- Target: `{definition.target}`",
            f"- Group: `{definition.group}`",
            f"- Description: {definition.description}",
            f"- Command: `{_redact(command_text)}`",
            *_node_identity_lines(),
            "",
            "## Failure",
            "",
            "```text",
            _redact(failure_message),
            "```",
            "",
            "## Remediation",
            "",
            f"Re-run `{_redact(command_text)}` on the affected node after applying a fix.",
        ]
    )


def _failure_update_comment(
    *,
    definition: HealthCheckDefinition,
    failure_message: str,
    command_text: str,
) -> str:
    return "\n".join(
        [
            f"Health target `{definition.target}` failed again.",
            "",
            f"- Command: `{_redact(command_text)}`",
            *_node_identity_lines(),
            "",
            "```text",
            _redact(failure_message),
            "```",
        ]
    )


def _recovery_comment(
    *,
    definition: HealthCheckDefinition,
    command_text: str,
) -> str:
    return "\n".join(
        [
            f"Health target `{definition.target}` recovered.",
            "",
            f"- Passing command: `{_redact(command_text)}`",
            *_node_identity_lines(),
            "",
            "Closing this issue because the target passed.",
        ]
    )


def _create_failure_issue(
    *,
    github_service,
    issue_client,
    definition: HealthCheckDefinition,
    fingerprint: str,
    failure_message: str,
    command_text: str,
):
    issue_kwargs = {
        "owner": issue_client.owner,
        "repository": issue_client.repository,
        "token": issue_client.token,
        "title": _failure_issue_title(definition),
        "body": _failure_issue_body(
            definition=definition,
            fingerprint=fingerprint,
            failure_message=failure_message,
            command_text=command_text,
        ),
    }
    try:
        response = github_service.create_issue(
            **issue_kwargs,
            labels=HEALTH_ISSUE_LABELS,
        )
        if response is None:
            logger.warning(
                "Retrying health GitHub issue creation without labels after labeled "
                "create returned no response for %s",
                HEALTH_ISSUE_LABELS,
            )
            return github_service.create_issue(**issue_kwargs)
        return response
    except Exception as exc:
        if not _label_error(exc):
            raise
        logger.warning("Retrying health GitHub issue creation without labels: %s", exc)
        return github_service.create_issue(**issue_kwargs)


def report_health_check_failure(
    *,
    definition: HealthCheckDefinition,
    failure_message: str,
    command_text: str,
) -> str | None:
    """Create or update a GitHub issue for one failed health target."""

    if not _reporting_enabled():
        return None

    from apps.repos.services import github as github_service

    fingerprint = health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    if not _try_reserve_failure_report(fingerprint):
        logger.info(
            "Skipping GitHub health issue report for %s inside cooldown",
            definition.target,
        )
        return None

    try:
        issue_client = github_service.GitHubIssue.from_active_repository()
        existing_issue = _find_health_issue(
            github_service=github_service,
            issue_client=issue_client,
            fingerprint=fingerprint,
        )
        if existing_issue is not None:
            issue_number = int(existing_issue.get("number") or 0)
            if issue_number <= 0:
                logger.warning(
                    "Health GitHub issue update skipped for %s; issue number missing",
                    definition.target,
                )
                _clear_failure_report(fingerprint)
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
                body=_failure_update_comment(
                    definition=definition,
                    failure_message=failure_message,
                    command_text=command_text,
                ),
            )
            with contextlib.suppress(Exception):
                response.close()
            return _issue_url(existing_issue)

        response = _create_failure_issue(
            github_service=github_service,
            issue_client=issue_client,
            definition=definition,
            fingerprint=fingerprint,
            failure_message=failure_message,
            command_text=command_text,
        )
        if response is None:
            _clear_failure_report(fingerprint)
            return None
        try:
            response_payload = response.json()
            if not isinstance(response_payload, dict):
                raise github_service.GitHubRepositoryError(
                    "Unable to decode GitHub issue response"
                )
        finally:
            with contextlib.suppress(Exception):
                response.close()
        return _issue_url(response_payload)
    except github_service.GitHubRepositoryError as exc:
        _clear_failure_report(fingerprint)
        logger.warning(
            "Health GitHub issue report failed for %s: %s",
            definition.target,
            exc,
        )
    except Exception:  # pragma: no cover - defensive guard
        _clear_failure_report(fingerprint)
        logger.exception(
            "Unexpected health GitHub issue report failure for %s",
            definition.target,
        )
    return None


def report_health_check_recovery(
    *,
    definition: HealthCheckDefinition,
    command_text: str,
) -> str | None:
    """Close the open GitHub health issue for a recovered target, if present."""

    if not _reporting_enabled():
        return None

    from apps.repos.services import github as github_service

    fingerprint = health_check_fingerprint(
        definition,
        command_text=command_text,
    )
    try:
        issue_client = github_service.GitHubIssue.from_active_repository()
        existing_issue = _find_health_issue(
            github_service=github_service,
            issue_client=issue_client,
            fingerprint=fingerprint,
            states=("open",),
        )
        if existing_issue is None:
            _clear_failure_report(fingerprint)
            return None
        issue_number = int(existing_issue.get("number") or 0)
        if issue_number <= 0:
            _clear_failure_report(fingerprint)
            return None

        response = github_service.create_issue_comment(
            issue_client.owner,
            issue_client.repository,
            issue_number=issue_number,
            token=issue_client.token,
            body=_recovery_comment(definition=definition, command_text=command_text),
        )
        with contextlib.suppress(Exception):
            response.close()
        response = github_service.close_issue(
            owner=issue_client.owner,
            repository=issue_client.repository,
            issue_number=issue_number,
            token=issue_client.token,
        )
        with contextlib.suppress(Exception):
            response.close()
        _clear_failure_report(fingerprint)
        return _issue_url(existing_issue)
    except github_service.GitHubRepositoryError as exc:
        logger.warning(
            "Health GitHub recovery report failed for %s: %s",
            definition.target,
            exc,
        )
    except Exception:  # pragma: no cover - defensive guard
        logger.exception(
            "Unexpected health GitHub recovery report failure for %s",
            definition.target,
        )
    return None
