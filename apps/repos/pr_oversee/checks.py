"""Check-rollup parsing, readiness gates, and dependency PR grouping."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .types import (
    CheckEntry,
    CheckRollupState,
    ReadinessGateResult,
    _coerce_list,
    _coerce_mapping,
)

GOOD_CHECKS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
BAD_CHECKS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PENDING_CHECKS = {
    "EXPECTED",
    "PENDING",
    "QUEUED",
    "REQUESTED",
    "IN_PROGRESS",
    "WAITING",
}
BAD_MERGE_STATES = {"BEHIND", "BLOCKED", "DIRTY", "UNKNOWN"}
VERSION_SUFFIX_SEPARATORS = "-_/"
ADVISORY_CHECK_LABELS = {"sonarcloud", "sonarcloudcodeanalysis"}
TRUSTED_SONAR_APP_LABELS = {"sonarcloud", "sonarqubecloud"}


def _author_login(pr: Mapping[str, Any]) -> str:
    author = pr.get("author")
    if isinstance(author, Mapping):
        return str(author.get("login") or "")
    return str(author or "")


def _check_name(check: Mapping[str, Any]) -> str:
    return str(
        check.get("name")
        or check.get("context")
        or check.get("workflowName")
        or check.get("workflow")
        or "check"
    )


def _check_group_key(check: Mapping[str, Any]) -> tuple[str, str, str]:
    app = _coerce_mapping(check.get("app"))
    return (
        _check_name(check),
        str(check.get("workflowName") or check.get("workflow") or ""),
        str(app.get("name") or ""),
    )


def _check_order_key(check: Mapping[str, Any], index: int) -> tuple[int, str]:
    for key in ("completedAt", "startedAt", "updatedAt", "createdAt"):
        value = str(check.get(key) or "")
        if value and not value.startswith("0001-"):
            return 1, value
    return 0, f"{index:08d}"


def _normalized_check_label(value: object) -> str:
    if value is None:
        return ""
    return "".join(character for character in str(value).casefold() if character.isalnum())


def _check_surface_labels(check: Mapping[str, Any]) -> tuple[object, ...]:
    return (
        _check_name(check),
        check.get("context"),
        check.get("workflowName"),
        check.get("workflow"),
    )


def _check_labels(check: Mapping[str, Any]) -> tuple[object, ...]:
    app = _coerce_mapping(check.get("app"))
    return (
        *_check_surface_labels(check),
        app.get("name"),
    )


def _has_advisory_sonar_label(check: Mapping[str, Any]) -> bool:
    return any(
        _normalized_check_label(label) in ADVISORY_CHECK_LABELS
        for label in _check_surface_labels(check)
    )


def is_advisory_check(check: Mapping[str, Any]) -> bool:
    """Return True for exact SonarCloud checks that must never block PR gates."""

    app = _coerce_mapping(check.get("app"))
    if "app" in check:
        return (
            _normalized_check_label(app.get("name")) in TRUSTED_SONAR_APP_LABELS
            and _has_advisory_sonar_label(check)
        )

    return False


def _check_entry(check: Mapping[str, Any]) -> dict[str, str]:
    name = _check_name(check)
    conclusion = str(check.get("conclusion") or "").upper()
    status = str(check.get("status") or "").upper()
    state = str(check.get("state") or "").upper()
    return {
        "name": name,
        "status": status,
        "state": state,
        "conclusion": conclusion,
        "value": conclusion or state or status or "UNKNOWN",
        "detailsUrl": str(
            check.get("detailsUrl") or check.get("targetUrl") or check.get("link") or ""
        ),
    }


LatestChecks = dict[
    tuple[str, str, str], tuple[tuple[int, str], int, Mapping[str, Any]]
]


def collect_latest_checks(
    pr: Mapping[str, Any],
) -> tuple[LatestChecks, list[dict[str, str]]]:
    latest: LatestChecks = {}
    superseded: list[dict[str, str]] = []
    for index, raw_check in enumerate(_coerce_list(pr.get("statusCheckRollup"))):
        check = _coerce_mapping(raw_check)
        key = _check_group_key(check)
        order = _check_order_key(check, index)
        previous = latest.get(key)
        if previous is None or order >= previous[0]:
            if previous is not None:
                superseded.append(_check_entry(previous[2]))
            latest[key] = (order, index, check)
        else:
            superseded.append(_check_entry(check))
    return latest, superseded


def _classified_check_bucket(entry: dict[str, str]) -> str:
    conclusion = entry["conclusion"]
    status = entry["status"]
    state = entry["state"]
    if conclusion in BAD_CHECKS or state in BAD_CHECKS:
        return "failing"
    if conclusion and conclusion not in GOOD_CHECKS:
        return "failing"
    if state and state not in GOOD_CHECKS and state != "COMPLETED":
        return "pending" if state in PENDING_CHECKS else "failing"
    if status and status not in {"COMPLETED", "SUCCESS"}:
        return "pending" if status in PENDING_CHECKS else "failing"
    return "passing"


def classify_latest_checks(latest: LatestChecks) -> dict[str, list[dict[str, str]]]:
    classified: dict[str, list[dict[str, str]]] = {
        "advisory": [],
        "failing": [],
        "pending": [],
        "passing": [],
    }
    for _order, _index, check in sorted(latest.values(), key=lambda item: item[1]):
        entry = _check_entry(check)
        if is_advisory_check(check):
            classified["advisory"].append(entry)
            continue
        classified[_classified_check_bucket(entry)].append(entry)
    return classified


def check_rollup_state(pr: Mapping[str, Any]) -> CheckRollupState:
    """Classify status check rollup entries as failing, pending, or passing."""

    latest, superseded = collect_latest_checks(pr)
    classified = classify_latest_checks(latest)
    return {
        "advisory": classified["advisory"],
        "failing": classified["failing"],
        "pending": classified["pending"],
        "passing": classified["passing"],
        "superseded": superseded,
    }


def merge_state_gate(pr: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    if pr.get("isDraft"):
        blockers.append("draft")
    merge_state = str(pr.get("mergeStateStatus") or "").upper()
    if merge_state in BAD_MERGE_STATES:
        blockers.append(f"merge_state:{merge_state}")
    elif not merge_state:
        warnings.append("merge_state:EMPTY")
    return blockers, warnings


def mergeable_gate(pr: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    mergeable = str(pr.get("mergeable") or "").upper()
    if mergeable in {"CONFLICTING", "FALSE"}:
        return [f"mergeable:{mergeable}"], []
    return [], []


def review_gate(
    pr: Mapping[str, Any], require_approval: bool
) -> tuple[list[str], list[str]]:
    review_decision = str(pr.get("reviewDecision") or "").upper()
    if review_decision in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}:
        return [f"review:{review_decision}"], []
    if require_approval and review_decision != "APPROVED":
        return [f"review:{review_decision or 'MISSING_APPROVAL'}"], []
    return [], []


def checks_gate(
    pr: Mapping[str, Any], allow_pending: bool
) -> tuple[list[str], list[str], CheckRollupState]:
    checks = check_rollup_state(pr)
    blockers = [f"check:{item['name']}:{item['value']}" for item in checks["failing"]]
    pending_values = [
        f"pending:{item['name']}:{item['value']}" for item in checks["pending"]
    ]
    if allow_pending:
        return blockers, pending_values, checks
    return blockers + pending_values, [], checks


def thread_gate(
    pr: Mapping[str, Any], require_conversation_resolution: bool
) -> tuple[list[str], list[str], int]:
    unresolved_threads = int(pr.get("unresolvedReviewThreadCount") or 0)
    if require_conversation_resolution and unresolved_threads:
        return (
            [f"review_threads:UNRESOLVED:{unresolved_threads}"],
            [],
            unresolved_threads,
        )
    return [], [], unresolved_threads


def readiness_gate(
    pr: Mapping[str, Any],
    *,
    require_approval: bool = False,
    allow_pending: bool = False,
    require_conversation_resolution: bool = True,
) -> ReadinessGateResult:
    """Return deterministic PR readiness blockers and warnings."""

    blockers: list[str] = []
    warnings: list[str] = []
    checks: CheckRollupState
    unresolved_threads = 0
    gate_results = [
        merge_state_gate(pr),
        mergeable_gate(pr),
        review_gate(pr, require_approval),
    ]
    for gate_blockers, gate_warnings in gate_results:
        blockers.extend(gate_blockers)
        warnings.extend(gate_warnings)

    check_blockers, check_warnings, checks = checks_gate(pr, allow_pending)
    blockers.extend(check_blockers)
    warnings.extend(check_warnings)
    thread_blockers, thread_warnings, unresolved_threads = thread_gate(
        pr, require_conversation_resolution
    )
    blockers.extend(thread_blockers)
    warnings.extend(thread_warnings)

    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "author": _author_login(pr),
        "url": pr.get("url"),
        "headRefName": pr.get("headRefName"),
        "headRefOid": pr.get("headRefOid"),
        "baseRefName": pr.get("baseRefName"),
        "baseRefOid": pr.get("baseRefOid"),
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "mergeStateStatus": pr.get("mergeStateStatus"),
        "mergeable": pr.get("mergeable"),
        "reviewDecision": pr.get("reviewDecision"),
        "checks": checks,
        "unresolvedReviewThreadCount": unresolved_threads,
        "updatedAt": pr.get("updatedAt"),
    }


def _split_marker(value: str, marker: str) -> tuple[str, str] | None:
    index = value.lower().find(marker)
    if index == -1:
        return None
    return value[:index], value[index + len(marker) :]


def _normalize_dependency_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _parse_dependency_title(title: str) -> tuple[str, str] | None:
    stripped = title.strip()
    lowered = stripped.lower()
    if lowered.startswith("bump "):
        body = stripped[5:].strip()
    elif lowered.startswith("update dependency "):
        body = stripped[len("update dependency ") :].strip()
    else:
        return None

    from_split = _split_marker(body, " from ")
    if from_split:
        name, remaining = from_split
        to_split = _split_marker(remaining, " to ")
        if to_split:
            to_parts = to_split[1].strip().split(maxsplit=1)
            if to_parts:
                return name.strip(), to_parts[0]
        return None

    to_split = _split_marker(body, " to ")
    if to_split:
        to_parts = to_split[1].strip().split(maxsplit=1)
        if to_parts:
            return to_split[0].strip(), to_parts[0]
    return None


def _looks_like_version_suffix(value: str) -> bool:
    normalized = value.strip().lstrip("vV")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    return (
        bool(normalized)
        and normalized[0].isdigit()
        and "." in normalized
        and all(character in allowed for character in normalized)
    )


def _version_suffix_match(value: str) -> tuple[int, str] | None:
    for index, character in enumerate(value):
        if character not in VERSION_SUFFIX_SEPARATORS:
            continue
        candidate = value[index + 1 :]
        if _looks_like_version_suffix(candidate):
            return index, candidate.lstrip("vV")
    return None


def _version_suffix(value: str) -> str:
    match = _version_suffix_match(value)
    return match[1] if match else ""


def _strip_version_suffix(value: str) -> str:
    match = _version_suffix_match(value)
    if not match:
        return value
    return value[: match[0]].rstrip(VERSION_SUFFIX_SEPARATORS)


def dependency_key(pr: Mapping[str, Any]) -> str:
    """Return a stable dependency grouping key for a PR."""

    title = str(pr.get("title") or "")
    title_parts = _parse_dependency_title(title)
    if title_parts:
        return _normalize_dependency_name(title_parts[0])
    head = str(pr.get("headRefName") or "").lower()
    if head.startswith("dependabot/"):
        parts = head.split("/", 2)
        head = parts[2] if len(parts) == 3 else parts[-1]
    head = _strip_version_suffix(head)
    return head or title.lower()


def dependency_target_version(pr: Mapping[str, Any]) -> str:
    """Best-effort dependency target version from title or branch."""

    title = str(pr.get("title") or "")
    title_parts = _parse_dependency_title(title)
    if title_parts:
        return title_parts[1]
    head = str(pr.get("headRefName") or "")
    return _version_suffix(head)


def is_dependency_pr(pr: Mapping[str, Any]) -> bool:
    """Return True when a PR appears to be a dependency update."""

    login = _author_login(pr).lower()
    title = str(pr.get("title") or "").lower()
    head = str(pr.get("headRefName") or "").lower()
    return (
        "dependabot" in login
        or "dependabot" in head
        or title.startswith("build(deps")
        or "bump " in title
    )


def dependency_duplicates(prs: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Group duplicate or superseded dependency PR candidates."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for pr in prs:
        if not is_dependency_pr(pr):
            continue
        key = dependency_key(pr)
        grouped.setdefault(key, []).append(
            {
                "number": pr.get("number"),
                "title": pr.get("title"),
                "headRefName": pr.get("headRefName"),
                "targetVersion": dependency_target_version(pr),
                "updatedAt": pr.get("updatedAt"),
                "url": pr.get("url"),
            }
        )

    duplicates: dict[str, Any] = {}
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        ordered = sorted(items, key=lambda item: str(item.get("updatedAt") or ""))
        duplicates[key] = {
            "items": ordered,
            "superseded": ordered[:-1],
            "preferred": ordered[-1],
        }
    return duplicates
