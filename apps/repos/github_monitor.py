"""GitHub issue monitoring that launches one operator terminal at a time."""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timezone as dt_timezone
from pathlib import Path
from typing import Any

import psutil
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from filelock import FileLock, Timeout

from apps.emails import mailer
from apps.emails.utils import resolve_recipient_fallbacks
from apps.features.models import Feature
from apps.features.utils import is_suite_feature_enabled
from apps.nodes.models import Node
from apps.repos.models import GitHubMonitorItem, GitHubMonitorTask, GitHubRepository
from apps.repos.services import github as github_service
from apps.skills.models import Skill

logger = logging.getLogger(__name__)

GITHUB_MONITOR_FEATURE_SLUG = "github-monitoring"
DEFAULT_REPOSITORY = "arthexis/arthexis"
INSTALL_HEALTH_TITLE = "Install health check is failing"
INSTALL_HEALTH_MARKER = "<!-- install-health-check-failure -->"
RELEASE_READINESS_TITLE = "Release Readiness Report"
RELEASE_READINESS_MARKER = "<!-- release-readiness-report -->"

PR_OVERSEE_POLICY_SKILL = "github-pr-oversee-policy"
ISSUE_OVERSEE_POLICY_SKILL = "github-issue-oversee-policy"
DEFAULT_POLICY_SKILLS = (PR_OVERSEE_POLICY_SKILL, ISSUE_OVERSEE_POLICY_SKILL)
REQUEUEABLE_MONITOR_STATUSES = {
    GitHubMonitorItem.Status.COMPLETED,
    GitHubMonitorItem.Status.CLOSED,
    GitHubMonitorItem.Status.TIMED_OUT,
    GitHubMonitorItem.Status.FAILED,
}
DEFAULT_TRUSTED_ISSUE_APPROVALS = 1000
TRUSTED_AUTHOR_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
DEFAULT_PR_APPROVAL_ACTOR = "arthexis"
DEFAULT_PR_APPROVAL_EMOJI = "+1"
REACTION_ALIASES = {
    "thumbs_up": "+1",
    "thumbsup": "+1",
    "rocket": "rocket",
    "eyes": "eyes",
}


GITHUB_MONITOR_FEATURE_FIELDS = {
    "display": "GitHub Monitoring",
    "source": Feature.Source.MAINSTREAM,
    "summary": (
        "Monitors configured GitHub readiness issues and pre-approved pull "
        "requests, then launches one local operator terminal at a time."
    ),
    "admin_requirements": (
        "Operators can enable the feature, configure monitored issue and PR "
        "signals, review queued monitor items, and install default issue/PR "
        "policy skills."
    ),
    "public_requirements": "No public interface; this is local operator automation.",
    "service_requirements": (
        "The scheduled monitor task must do nothing until monitor task rows exist. "
        "When configured, it polls GitHub issues and PRs, honors explicit approval "
        "reactions, maintains one active terminal, times out inactive consoles, "
        "and emails admins on failures."
    ),
    "admin_views": [
        "admin:features_feature_changelist",
        "admin:repos_githubmonitortask_changelist",
        "admin:repos_githubmonitoritem_changelist",
    ],
    "public_views": [],
    "service_views": [
        "Command: python manage.py gh_monitor",
        "apps.repos.tasks.monitor_github_readiness",
    ],
    "code_locations": [
        "apps/repos/github_monitor.py",
        "apps/repos/management/commands/gh_monitor.py",
        "apps/repos/models/monitoring.py",
        "apps/repos/tasks.py",
    ],
    "protocol_coverage": {},
    "metadata": {
        "setup_command": "python manage.py gh_monitor configure --write",
        "default_repository": DEFAULT_REPOSITORY,
        "patchwork_directory": "ARTHEXIS_PATCHWORK_DIR or ~/patchwork",
    },
}


PR_OVERSEE_POLICY_MARKDOWN = """---
name: github-pr-oversee-policy
description: Default suite policy for overseeing GitHub pull requests from an Arthexis monitor-launched operator console.
---

# GitHub PR Oversee Policy

Use the source-backed suite commands before hand-written GitHub routines.

## Required first pass

1. Refresh live PR state with `manage.py pr_oversee --repo <owner/repo> --json inspect --pr <number>`.
2. Run `gate`, `comments --unresolved`, `ci --logs`, `hygiene`, and `test-plan` before claiming readiness.
3. Treat unresolved review threads, failed required checks, conflicts, moving heads, and missing human approvals as blockers.
4. Use an isolated patchwork worktree for code edits or local validation when the PR branch is not the active checkout.
5. Merge only when the deterministic gate is clean and the operator request includes merge authority.

## Guardrails

- Never reset or clean an unrelated dirty checkout.
- Do not decide away substantive review comments.
- Record exact commands, PR number, head SHA, and final state in the terminal summary.
"""


ISSUE_OVERSEE_POLICY_MARKDOWN = """---
name: github-issue-oversee-policy
description: Default suite policy for working GitHub issues surfaced by Arthexis GitHub Monitoring.
---

# GitHub Issue Oversee Policy

Use this policy when a monitor-launched terminal receives an install-health,
release-readiness, or other GitHub issue prompt.

## Required first pass

1. Read the issue body and latest comments before changing code.
2. Reproduce or verify the reported failure with the closest deterministic suite command.
3. Create a focused branch whose name includes the affected app or subsystem.
4. Open a PR linked to the issue after the fix is pushed.
5. Use the PR oversee policy to watch checks and reviewer feedback.

## Guardrails

- Keep fixes scoped to the issue unless validation exposes a directly related failure.
- Do not close issues manually when workflow automation is expected to close them after recovery.
- If the issue is a release-readiness report, verify human approval separately from bot comments.
- Use `gh_monitor heartbeat` while working and `gh_monitor complete` only when the monitor item no longer needs this console.
"""


DEFAULT_PROMPT_TEMPLATE = """You were launched by Arthexis GitHub Monitoring.

Repository: {repository}
Monitor task: {task_display} ({task_name})
Issue: #{issue_number} {issue_title}
Issue URL: {issue_url}

Policy skills available in the suite:
{policy_skills}

Embedded policy reference:
{policy_markdown}

Before work and after material progress, update activity:
{heartbeat_command}

When this monitor item no longer needs an active console, mark it complete:
{complete_command}

Current issue body:
{issue_body}
"""


PR_OVERSEE_PROMPT_TEMPLATE = """You were launched by Arthexis GitHub Monitoring.

Repository: {repository}
Monitor task: {task_display} ({task_name})
Pull request: #{issue_number} {issue_title}
Pull request URL: {issue_url}
Approved by: {approved_by} reacting {approval_emoji}
Approved head SHA: {approved_head_sha}

Policy skills available in the suite:
{policy_skills}

Embedded policy reference:
{policy_markdown}

Use this controlled workflow first:
{pr_oversee_monitor_command}

Before work and after material progress, update activity:
{heartbeat_command}

When this monitor item no longer needs an active console, mark it complete:
{complete_command}

Do not merge if the PR head differs from the approved head SHA. If the monitor
stops for a manual decision, summarize the blocker and leave this item active.

Current PR body:
{issue_body}
"""


@dataclass(frozen=True)
class DefaultMonitorTaskSpec:
    name: str
    display: str
    issue_title: str
    issue_marker: str
    terminal_title: str
    terminal_state_key: str
    target_type: str = GitHubMonitorTask.TargetType.ISSUE
    label_filter: str = ""
    require_approval_reaction: bool = False
    approval_actor: str = ""
    approval_emoji: str = ""
    prompt_template: str = DEFAULT_PROMPT_TEMPLATE
    skill_slugs: tuple[str, ...] = DEFAULT_POLICY_SKILLS


DEFAULT_MONITOR_TASKS = (
    DefaultMonitorTaskSpec(
        name="install-health",
        display="Install Health",
        issue_title=INSTALL_HEALTH_TITLE,
        issue_marker=INSTALL_HEALTH_MARKER,
        terminal_title="Arthexis Install Health",
        terminal_state_key="gh-monitor-install-health",
    ),
    DefaultMonitorTaskSpec(
        name="release-readiness",
        display="Release Readiness",
        issue_title=RELEASE_READINESS_TITLE,
        issue_marker=RELEASE_READINESS_MARKER,
        terminal_title="Arthexis Release Readiness",
        terminal_state_key="gh-monitor-release-readiness",
    ),
    DefaultMonitorTaskSpec(
        name="approved-pr-oversee",
        display="Approved PR Oversee",
        issue_title="",
        issue_marker="",
        terminal_title="Arthexis PR Oversee",
        terminal_state_key="gh-monitor-pr-oversee",
        target_type=GitHubMonitorTask.TargetType.PULL_REQUEST,
        require_approval_reaction=True,
        approval_actor=DEFAULT_PR_APPROVAL_ACTOR,
        approval_emoji=DEFAULT_PR_APPROVAL_EMOJI,
        prompt_template=PR_OVERSEE_PROMPT_TEMPLATE,
        skill_slugs=(PR_OVERSEE_POLICY_SKILL,),
    ),
)


def desktop_ui_enabled() -> bool:
    return bool(
        getattr(settings, "DESKTOP_UI_ENABLED", False)
        or getattr(settings, "DESKTOP_UI", False)
    )


def celery_lock_enabled() -> bool:
    return (Path(settings.BASE_DIR) / ".locks" / "celery.lck").exists()


def monitor_lock_path() -> Path:
    return Path(settings.BASE_DIR) / ".locks" / "github-monitor.lck"


def patchwork_dir() -> Path:
    configured = (
        os.environ.get("ARTHEXIS_PATCHWORK_DIR")
        or getattr(settings, "ARTHEXIS_PATCHWORK_DIR", "")
    )
    if configured:
        return Path(str(configured)).expanduser()
    return Path.home() / "patchwork"


def ensure_workstation_paths(*, write: bool) -> dict[str, Any]:
    root = patchwork_dir()
    result = {"patchwork_dir": str(root), "exists": root.exists(), "write": write}
    if write:
        root.mkdir(parents=True, exist_ok=True)
        result["exists"] = root.exists()
    return result


def github_monitor_enabled() -> bool:
    return is_suite_feature_enabled(GITHUB_MONITOR_FEATURE_SLUG, default=False)


def _parse_repository_slug(repository: str) -> tuple[str, str]:
    owner, separator, name = (repository or "").strip().partition("/")
    if not owner or not separator or not name:
        raise ValueError("Repository must be in owner/name form.")
    return owner, name


def _default_policy_specs() -> tuple[dict[str, str], ...]:
    return (
        {
            "slug": PR_OVERSEE_POLICY_SKILL,
            "title": "GitHub PR Oversee Policy",
            "description": "Default suite policy for deterministic GitHub PR oversight.",
            "markdown": PR_OVERSEE_POLICY_MARKDOWN,
        },
        {
            "slug": ISSUE_OVERSEE_POLICY_SKILL,
            "title": "GitHub Issue Oversee Policy",
            "description": "Default suite policy for GitHub issue triage and PR follow-through.",
            "markdown": ISSUE_OVERSEE_POLICY_MARKDOWN,
        },
    )


def ensure_github_monitor_feature(
    *, write: bool, enabled: bool = True
) -> dict[str, Any]:
    existing = Feature.all_objects.filter(slug=GITHUB_MONITOR_FEATURE_SLUG).first()
    action = "create" if existing is None else "update"
    if not write:
        return {"slug": GITHUB_MONITOR_FEATURE_SLUG, "action": action}

    feature = existing or Feature(slug=GITHUB_MONITOR_FEATURE_SLUG)
    for field, value in GITHUB_MONITOR_FEATURE_FIELDS.items():
        setattr(feature, field, value)
    feature.is_enabled = enabled
    feature.is_deleted = False
    feature.save()
    Feature.all_objects.filter(pk=feature.pk).update(is_seed_data=True)
    return {"slug": feature.slug, "action": action, "enabled": feature.is_enabled}


def ensure_default_policy_skills(*, write: bool) -> dict[str, Any]:
    planned = []
    for spec in _default_policy_specs():
        existing = Skill.all_objects.filter(slug=spec["slug"]).first()
        action = "create" if existing is None else "update"
        planned.append({"slug": spec["slug"], "action": action})
        if not write:
            continue
        if existing is None:
            skill = Skill.objects.create(
                slug=spec["slug"],
                title=spec["title"],
                description=spec["description"],
                markdown=spec["markdown"],
            )
        else:
            skill = existing
            skill.title = spec["title"]
            skill.description = spec["description"]
            skill.markdown = spec["markdown"]
            skill.is_deleted = False
            skill.save(update_fields=["title", "description", "markdown", "is_deleted"])
        Skill.all_objects.filter(pk=skill.pk).update(is_seed_data=True)
    return {"write": write, "skills": planned}


def configure_default_monitoring(
    *,
    repository: str = DEFAULT_REPOSITORY,
    codex_command: str = "codex",
    inactivity_timeout_minutes: int = 45,
    write: bool = False,
) -> dict[str, Any]:
    owner, name = _parse_repository_slug(repository)
    task_summaries = []
    repo_obj = None
    if write:
        repo_obj, _ = GitHubRepository.all_objects.update_or_create(
            owner=owner,
            name=name,
            defaults={
                "html_url": f"https://github.com/{owner}/{name}",
                "is_deleted": False,
            },
        )

    for spec in DEFAULT_MONITOR_TASKS:
        existing = GitHubMonitorTask.all_objects.filter(name=spec.name).first()
        action = "create" if existing is None else "update"
        task_summaries.append({"name": spec.name, "action": action})
        if not write:
            continue
        task = existing or GitHubMonitorTask(name=spec.name)
        task.display = spec.display
        task.repository = repo_obj
        task.enabled = True
        task.target_type = spec.target_type
        task.issue_title = spec.issue_title
        task.issue_marker = spec.issue_marker
        task.label_filter = spec.label_filter
        task.require_approval_reaction = spec.require_approval_reaction
        task.approval_actor = spec.approval_actor
        task.approval_emoji = spec.approval_emoji
        task.terminal_title = spec.terminal_title
        task.terminal_state_key = spec.terminal_state_key
        task.codex_command = codex_command.strip() or "codex"
        task.prompt_template = spec.prompt_template
        task.skill_slugs = list(spec.skill_slugs)
        task.inactivity_timeout_minutes = max(int(inactivity_timeout_minutes), 1)
        task.is_deleted = False
        task.save()

    return {
        "repository": f"{owner}/{name}",
        "write": write,
        "feature": ensure_github_monitor_feature(write=write, enabled=True),
        "policy_skills": ensure_default_policy_skills(write=write),
        "workstation": ensure_workstation_paths(write=write),
        "tasks": task_summaries,
    }


def _command_available(command: str) -> bool:
    try:
        parts = _split_codex_command(command) if command else []
    except ValueError:
        return False
    if not parts:
        return False
    executable = parts[0]
    if Path(executable).exists():
        return True
    return shutil.which(executable) is not None


def _terminal_launcher_available() -> bool:
    if os.name == "nt":
        return bool(shutil.which("wt") or shutil.which("powershell"))
    return bool(
        shutil.which("x-terminal-emulator")
        or shutil.which("gnome-terminal")
        or shutil.which("konsole")
        or shutil.which("xterm")
    )


def _git_status_clean() -> bool:
    base_dir = Path(settings.BASE_DIR)
    if not (base_dir / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def evaluate_readiness() -> dict[str, Any]:
    token_configured = False
    token_error = ""
    try:
        token_configured = bool(github_service.get_github_issue_token())
    except Exception as exc:
        token_error = str(exc)

    enabled_tasks = GitHubMonitorTask.objects.filter(enabled=True).count()
    active_items = GitHubMonitorItem.objects.filter(
        status=GitHubMonitorItem.Status.ACTIVE
    ).count()
    queued_items = GitHubMonitorItem.objects.filter(
        status=GitHubMonitorItem.Status.QUEUED
    ).count()
    feature_enabled = github_monitor_enabled()
    desktop_enabled = desktop_ui_enabled()
    default_codex_command = (
        GitHubMonitorTask.objects.filter(enabled=True)
        .values_list("codex_command", flat=True)
        .first()
        or "codex"
    )
    patchwork = patchwork_dir()
    codex_available = _command_available(default_codex_command)
    gh_available = shutil.which("gh") is not None
    terminal_available = _terminal_launcher_available()
    git_clean = _git_status_clean()
    pr_oversee_available = (
        Path(settings.BASE_DIR)
        / "apps"
        / "repos"
        / "management"
        / "commands"
        / "pr_oversee.py"
    ).exists()

    return {
        "feature_enabled": feature_enabled,
        "token_configured": token_configured,
        "token_error": token_error,
        "desktop_ui_enabled": desktop_enabled,
        "celery_lock_enabled": celery_lock_enabled(),
        "email_configured": mailer.can_send_email(),
        "codex_command": default_codex_command,
        "codex_command_found": codex_available,
        "gh_command_found": gh_available,
        "terminal_launcher_found": terminal_available,
        "patchwork_dir": str(patchwork),
        "patchwork_dir_exists": patchwork.exists(),
        "git_checkout_clean": git_clean,
        "pr_oversee_available": pr_oversee_available,
        "configured_tasks": enabled_tasks,
        "active_items": active_items,
        "queued_items": queued_items,
        "ready": bool(
            feature_enabled and token_configured and desktop_enabled and enabled_tasks
            and codex_available and gh_available and terminal_available
            and patchwork.exists() and git_clean and pr_oversee_available
        ),
    }


def _trusted_issue_authors(task: GitHubMonitorTask) -> set[str]:
    configured = getattr(settings, "GITHUB_MONITOR_TRUSTED_AUTHORS", ())
    if isinstance(configured, str):
        configured_authors = configured.replace(",", " ").split()
    elif isinstance(configured, Mapping):
        configured_authors = [
            author for author, enabled in configured.items() if enabled
        ]
    else:
        try:
            configured_authors = iter(configured)
        except TypeError:
            configured_authors = ()
    trusted = {
        str(author).strip().lower()
        for author in configured_authors
        if str(author).strip()
    }
    repository_owner = str(task.repository.owner or "").strip().lower()
    if repository_owner:
        trusted.add(repository_owner)
    return trusted


def _trusted_issue_approval_threshold() -> int:
    raw_threshold = getattr(
        settings,
        "GITHUB_MONITOR_TRUSTED_ISSUE_APPROVALS",
        DEFAULT_TRUSTED_ISSUE_APPROVALS,
    )
    try:
        return max(0, int(raw_threshold))
    except (TypeError, ValueError):
        return DEFAULT_TRUSTED_ISSUE_APPROVALS


def _normalize_reaction_emoji(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    return REACTION_ALIASES.get(cleaned, cleaned)


def _label_names(item: Mapping[str, object]) -> set[str]:
    labels = item.get("labels") or []
    names: set[str] = set()
    if not isinstance(labels, list):
        return names
    for label in labels:
        if isinstance(label, Mapping):
            name = label.get("name")
        else:
            name = label
        cleaned = str(name or "").strip().lower()
        if cleaned:
            names.add(cleaned)
    return names


def _label_filter_matches(task: GitHubMonitorTask, item: Mapping[str, object]) -> bool:
    label_filter = str(task.label_filter or "").strip().lower()
    if not label_filter:
        return True
    return label_filter in _label_names(item)


def _parse_github_datetime(value: object):
    parsed = parse_datetime(str(value or "").strip())
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone=dt_timezone.utc)
    return parsed


def _reaction_approval(
    task: GitHubMonitorTask,
    reactions: list[Mapping[str, object]],
) -> dict[str, object] | None:
    required_actor = str(task.approval_actor or "").strip().lower()
    required_emoji = _normalize_reaction_emoji(task.approval_emoji or "+1")
    for reaction in reactions:
        content = _normalize_reaction_emoji(str(reaction.get("content") or ""))
        if content != required_emoji:
            continue
        try:
            actor = str((reaction.get("user") or {}).get("login") or "").strip()
        except (AttributeError, TypeError):
            actor = ""
        if required_actor and actor.lower() != required_actor:
            continue
        return {
            "approved_by": actor,
            "approval_emoji": content,
            "approved_at": _parse_github_datetime(reaction.get("created_at")),
        }
    return None


def _pull_request_last_updated_at(pull_request: Mapping[str, object]):
    if not isinstance(pull_request, Mapping):
        return None
    return _parse_github_datetime(pull_request.get("updated_at"))


def _approval_covers_head(
    *,
    approval: Mapping[str, object],
    pull_request: Mapping[str, object],
) -> bool:
    approved_at = approval.get("approved_at")
    head_commit_timestamp = _pull_request_last_updated_at(pull_request)
    if approved_at is None or head_commit_timestamp is None:
        return False
    return approved_at >= head_commit_timestamp


def _issue_matches(task: GitHubMonitorTask, item: Mapping[str, object]) -> bool:
    if task.target_type != GitHubMonitorTask.TargetType.ISSUE:
        return False
    if "pull_request" in item:
        return False
    if task.issue_title and str(item.get("title") or "").strip() != task.issue_title:
        return False
    marker = (task.issue_marker or "").strip()
    if marker and marker not in str(item.get("body") or ""):
        return False
    if not _label_filter_matches(task, item):
        return False
    try:
        author_login = (
            str((item.get("user") or {}).get("login") or "").strip().lower()
        )
    except (AttributeError, TypeError, ValueError):
        author_login = ""
    try:
        approvals = int((item.get("reactions") or {}).get("+1") or 0)
    except (AttributeError, TypeError, ValueError):
        approvals = 0
    author_association = str(item.get("author_association") or "").strip().upper()
    if (
        author_login not in _trusted_issue_authors(task)
        and author_association not in TRUSTED_AUTHOR_ASSOCIATIONS
        and approvals < _trusted_issue_approval_threshold()
    ):
        return False
    return isinstance(item.get("number"), int)


def _pull_request_matches(
    task: GitHubMonitorTask,
    pull_request: Mapping[str, object],
) -> bool:
    if task.target_type != GitHubMonitorTask.TargetType.PULL_REQUEST:
        return False
    if pull_request.get("draft"):
        return False
    if (
        task.issue_title
        and str(pull_request.get("title") or "").strip() != task.issue_title
    ):
        return False
    marker = (task.issue_marker or "").strip()
    if marker and marker not in str(pull_request.get("body") or ""):
        return False
    if not _label_filter_matches(task, pull_request):
        return False
    return isinstance(pull_request.get("number"), int)


def _fingerprint(task: GitHubMonitorTask, issue_number: int) -> str:
    source = f"{task.repository.slug}|{task.name}|{task.target_type}|{issue_number}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _issue_body(item: Mapping[str, object], *, limit: int = 12000) -> str:
    body = str(item.get("body") or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit] + "\n\n[truncated by gh_monitor]"


def _upsert_monitor_item(
    task: GitHubMonitorTask,
    issue: Mapping[str, object],
    *,
    now,
    approval: Mapping[str, object] | None = None,
    target_head_sha: str = "",
) -> GitHubMonitorItem:
    issue_number = int(issue["number"])
    fingerprint = _fingerprint(task, issue_number)
    approval = approval or {}
    defaults = {
        "target_type": task.target_type,
        "issue_title": str(issue.get("title") or ""),
        "issue_url": str(issue.get("html_url") or ""),
        "issue_state": str(issue.get("state") or "open"),
        "issue_body": _issue_body(issue),
        "target_head_sha": str(target_head_sha or ""),
        "approved_by": str(approval.get("approved_by") or ""),
        "approval_emoji": str(approval.get("approval_emoji") or ""),
        "approved_at": approval.get("approved_at"),
        "approved_head_sha": str(
            target_head_sha or approval.get("approved_head_sha") or ""
        ),
        "last_seen_at": now,
    }
    item = GitHubMonitorItem.all_objects.filter(fingerprint=fingerprint).first()
    if item is None:
        item = GitHubMonitorItem.all_objects.filter(
            task=task,
            issue_number=issue_number,
        ).first()
    if item is None:
        return GitHubMonitorItem.objects.create(
            task=task,
            fingerprint=fingerprint,
            issue_number=issue_number,
            **defaults,
        )

    if (
        task.target_type == GitHubMonitorTask.TargetType.PULL_REQUEST
        and item.approved_head_sha
        and target_head_sha
        and item.approved_head_sha != target_head_sha
    ):
        approved_at = defaults.get("approved_at")
        if not approved_at or (item.approved_at and approved_at <= item.approved_at):
            item.mark_status(
                GitHubMonitorItem.Status.CLOSED,
                failure_message=(
                    "Approval is stale because the PR head changed from "
                    f"{item.approved_head_sha} to {target_head_sha}."
                ),
            )
            return item

    for field, value in defaults.items():
        setattr(item, field, value)
    update_fields = [*defaults.keys(), "is_deleted"]
    if item.fingerprint != fingerprint:
        item.fingerprint = fingerprint
        update_fields.append("fingerprint")
    if (
        item.status in REQUEUEABLE_MONITOR_STATUSES
        and defaults["issue_state"].lower() == "open"
    ):
        item.status = GitHubMonitorItem.Status.QUEUED
        item.queued_at = now
        item.launched_at = None
        item.last_activity_at = None
        item.completed_at = None
        item.failure_message = ""
        item.prompt = ""
        item.terminal_state_key = ""
        item.terminal_pid_file = ""
        update_fields.extend(
            [
                "status",
                "queued_at",
                "launched_at",
                "last_activity_at",
                "completed_at",
                "failure_message",
                "prompt",
                "terminal_state_key",
                "terminal_pid_file",
            ]
        )
    item.is_deleted = False
    item.save(update_fields=update_fields)
    return item


def _approval_for_target(
    task: GitHubMonitorTask,
    *,
    token: str,
    issue_number: int,
) -> dict[str, object] | None:
    if not task.require_approval_reaction:
        return {}
    reactions = list(
        github_service.fetch_issue_reactions(
            token=token,
            owner=task.repository.owner,
            name=task.repository.name,
            issue_number=issue_number,
        )
    )
    return _reaction_approval(task, reactions)


def sync_monitor_items(*, token: str | None = None, now=None) -> dict[str, Any]:
    now = now or timezone.now()
    token = token or github_service.get_github_issue_token()
    created_or_seen = []
    matched_fingerprints: dict[int, set[str]] = {}
    tasks = GitHubMonitorTask.objects.select_related("repository").filter(enabled=True)

    for task in tasks:
        seen_for_task: set[str] = set()
        if task.target_type == GitHubMonitorTask.TargetType.PULL_REQUEST:
            for pull_request in github_service.fetch_repository_pull_requests(
                token=token,
                owner=task.repository.owner,
                name=task.repository.name,
            ):
                issue_number = int(pull_request.get("number") or 0)
                if issue_number <= 0:
                    continue
                if not _pull_request_matches(task, pull_request):
                    continue
                approval = _approval_for_target(
                    task,
                    token=token,
                    issue_number=issue_number,
                )
                if approval is None:
                    continue
                try:
                    head_sha = str((pull_request.get("head") or {}).get("sha") or "")
                except (AttributeError, TypeError):
                    head_sha = ""
                if not head_sha:
                    continue
                if task.require_approval_reaction and not _approval_covers_head(
                    approval=approval,
                    pull_request=pull_request,
                ):
                    continue
                item = _upsert_monitor_item(
                    task,
                    pull_request,
                    now=now,
                    approval=approval,
                    target_head_sha=head_sha,
                )
                seen_for_task.add(item.fingerprint)
                created_or_seen.append(item.pk)
        else:
            for issue in github_service.fetch_repository_issues(
                token=token,
                owner=task.repository.owner,
                name=task.repository.name,
            ):
                if not _issue_matches(task, issue):
                    continue
                issue_number = int(issue.get("number") or 0)
                approval = _approval_for_target(
                    task,
                    token=token,
                    issue_number=issue_number,
                )
                if approval is None:
                    continue
                item = _upsert_monitor_item(
                    task,
                    issue,
                    now=now,
                    approval=approval,
                )
                seen_for_task.add(item.fingerprint)
                created_or_seen.append(item.pk)
        matched_fingerprints[task.pk] = seen_for_task

        stale_queued = task.items.filter(status=GitHubMonitorItem.Status.QUEUED)
        if seen_for_task:
            stale_queued = stale_queued.exclude(fingerprint__in=seen_for_task)
        stale_queued.update(
            status=GitHubMonitorItem.Status.CLOSED,
            issue_state="closed",
            completed_at=now,
        )

    return {
        "matched": len(created_or_seen),
        "matched_item_ids": created_or_seen,
        "tasks_checked": len(matched_fingerprints),
    }


def _terminal_running(pid_file: Path) -> bool:
    from apps.terminals.tasks import _terminal_running as terminal_running

    return terminal_running(pid_file)


def _read_terminal_pid(pid_file: Path) -> int | None:
    from apps.terminals.tasks import _read_pid_file

    pid, _ = _read_pid_file(pid_file)
    return pid


def _terminate_terminal(pid_file: Path) -> bool:
    if not _terminal_running(pid_file):
        return False
    pid = _read_terminal_pid(pid_file)
    if not pid:
        pid_file.unlink(missing_ok=True)
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
            text=True,
        )
    else:
        try:
            process = psutil.Process(pid)
            for child in process.children(recursive=True):
                child.terminate()
            process.terminate()
        except psutil.Error:
            return False
    if _terminal_running(pid_file):
        return False
    pid_file.unlink(missing_ok=True)
    return True


def _active_item() -> GitHubMonitorItem | None:
    return (
        GitHubMonitorItem.objects.select_related("task", "task__repository")
        .filter(status=GitHubMonitorItem.Status.ACTIVE)
        .order_by("launched_at", "id")
        .first()
    )


def _item_is_inactive(item: GitHubMonitorItem, *, now) -> bool:
    activity_at = item.last_activity_at or item.launched_at or item.queued_at
    return now - activity_at > item.task.inactivity_timeout


def maintain_active_terminal(*, now=None) -> dict[str, Any]:
    now = now or timezone.now()
    item = _active_item()
    if item is None:
        return {"active": None, "released": False, "reason": "none"}

    pid_file = Path(item.terminal_pid_file) if item.terminal_pid_file else None
    running = bool(pid_file and _terminal_running(pid_file))
    if not running:
        item.mark_status(GitHubMonitorItem.Status.CLOSED)
        return {
            "active": None,
            "released": True,
            "reason": "terminal_closed",
            "item": item.pk,
        }

    if _item_is_inactive(item, now=now):
        if not _terminate_terminal(pid_file):
            return {
                "active": item.pk,
                "released": False,
                "reason": "terminate_failed",
            }
        item.mark_status(GitHubMonitorItem.Status.TIMED_OUT)
        return {"active": None, "released": True, "reason": "inactive", "item": item.pk}

    return {"active": item.pk, "released": False, "reason": "running"}


def _quote_command_path(path: str | Path) -> str:
    text = str(path)
    return f'"{text}"' if " " in text else text


def _manage_command(action: str, item: GitHubMonitorItem) -> str:
    manage_py = Path(settings.BASE_DIR) / "manage.py"
    return (
        f"{_quote_command_path(sys.executable)} {_quote_command_path(manage_py)} "
        f"gh_monitor {action} --item {item.pk}"
    )


def _state_key_for_item(item: GitHubMonitorItem) -> str:
    suffix = f"-{item.pk}"
    base = item.task.terminal_state_key[: 120 - len(suffix)].strip("-")
    return f"{base}{suffix}"


def _policy_skill_lines(item: GitHubMonitorItem) -> str:
    slugs = item.task.skill_slugs if isinstance(item.task.skill_slugs, list) else []
    if not slugs:
        slugs = list(DEFAULT_POLICY_SKILLS)
    return "\n".join(f"- {slug}" for slug in slugs)


def _policy_skill_markdown(item: GitHubMonitorItem) -> str:
    slugs = item.task.skill_slugs if isinstance(item.task.skill_slugs, list) else []
    if not slugs:
        slugs = list(DEFAULT_POLICY_SKILLS)
    skills = Skill.objects.filter(slug__in=slugs).order_by("slug")
    sections = [skill.markdown.strip() for skill in skills if skill.markdown.strip()]
    return "\n\n---\n\n".join(sections) or "No policy skill content is installed."


def _shell_command_line(parts: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(parts)
    return shlex.join(parts)


def _pr_oversee_monitor_command(item: GitHubMonitorItem) -> str:
    parts = [
        sys.executable,
        str(Path(settings.BASE_DIR) / "manage.py"),
        "pr_oversee",
        "--repo",
        item.task.repository.slug,
        "monitor",
        "--pr",
        str(item.issue_number),
        "--patchwork-dir",
        str(patchwork_dir()),
        "--merge",
        "--cleanup",
        "--delete-branch",
        "--write",
    ]
    if item.approved_head_sha:
        parts.extend(["--expected-head-sha", item.approved_head_sha])
    return _shell_command_line(parts)


def build_monitor_prompt(item: GitHubMonitorItem) -> str:
    template = item.task.prompt_template or DEFAULT_PROMPT_TEMPLATE
    values = {
        "repository": item.task.repository.slug,
        "task_name": item.task.name,
        "task_display": item.task.display,
        "issue_number": item.issue_number,
        "issue_title": item.issue_title,
        "issue_url": item.issue_url,
        "issue_body": item.issue_body,
        "target_type": item.target_type,
        "target_head_sha": item.target_head_sha,
        "approved_by": item.approved_by,
        "approval_emoji": item.approval_emoji,
        "approved_at": item.approved_at.isoformat() if item.approved_at else "",
        "approved_head_sha": item.approved_head_sha,
        "policy_skills": _policy_skill_lines(item),
        "policy_markdown": _policy_skill_markdown(item),
        "patchwork_dir": str(patchwork_dir()),
        "pr_oversee_monitor_command": _pr_oversee_monitor_command(item),
        "heartbeat_command": _manage_command("heartbeat", item),
        "complete_command": _manage_command("complete", item),
        "inactivity_timeout_minutes": item.task.inactivity_timeout_minutes,
    }
    return template.format_map(values)


def _split_codex_command(command: str) -> list[str]:
    command = command.strip() or "codex"
    return [part.strip('"') for part in shlex.split(command, posix=os.name != "nt")]


def _run_terminal_command(item: GitHubMonitorItem) -> list[str]:
    return [
        sys.executable,
        str(Path(settings.BASE_DIR) / "manage.py"),
        "gh_monitor",
        "run-terminal",
        "--item",
        str(item.pk),
    ]


def run_terminal_item(*, item_id: int, heartbeat_seconds: int = 60) -> dict[str, Any]:
    item = _resolve_item(item_id=item_id)
    if item.status != GitHubMonitorItem.Status.ACTIVE:
        raise RuntimeError("run-terminal requires an active monitor item.")
    active = _active_item()
    if active is None or active.pk != item.pk:
        active_pk = active.pk if active is not None else "none"
        raise RuntimeError(
            f"Cannot run monitor item {item.pk} while item {active_pk} is active."
        )
    try:
        prompt = item.prompt or build_monitor_prompt(item)
        command = [*_split_codex_command(item.task.codex_command), prompt]
        item.touch_activity()
        process = subprocess.Popen(command, cwd=Path(settings.BASE_DIR))
    except Exception as exc:
        item.mark_status(GitHubMonitorItem.Status.FAILED, failure_message=str(exc))
        notify_admins_of_failure(
            "Arthexis GitHub monitor terminal command failed",
            f"Failed to start Codex for monitor item {item.pk}: {exc}",
        )
        raise
    while process.poll() is None:
        time.sleep(max(int(heartbeat_seconds), 1))
        item.touch_activity()
    item.touch_activity()
    if process.returncode:
        item.mark_status(
            GitHubMonitorItem.Status.FAILED,
            failure_message=f"Codex command exited with {process.returncode}.",
        )
        notify_admins_of_failure(
            "Arthexis GitHub monitor terminal exited with failure",
            (
                f"Codex exited with {process.returncode} for monitor item "
                f"{item.pk}: {item.issue_url}"
            ),
        )
    return {"item": item.pk, "returncode": process.returncode}


def _launch_command(command: list[str], *, title: str, state_key: str) -> Path:
    from apps.terminals.tasks import launch_command_in_terminal

    return launch_command_in_terminal(
        command,
        title=title,
        state_key=state_key,
        working_directory=Path(settings.BASE_DIR),
    )


def launch_next_queued_item(*, now=None) -> dict[str, Any]:
    if not desktop_ui_enabled():
        return {"launched": None, "reason": "desktop_ui_disabled"}

    now = now or timezone.now()
    item = (
        GitHubMonitorItem.objects.select_related("task", "task__repository")
        .filter(status=GitHubMonitorItem.Status.QUEUED)
        .order_by("queued_at", "id")
        .first()
    )
    if item is None:
        return {"launched": None, "reason": "queue_empty"}

    try:
        state_key = _state_key_for_item(item)
        prompt = build_monitor_prompt(item)
        item.status = GitHubMonitorItem.Status.ACTIVE
        item.prompt = prompt
        item.terminal_state_key = state_key
        item.launched_at = now
        item.last_activity_at = now
        item.save(
            update_fields=[
                "status",
                "prompt",
                "terminal_state_key",
                "launched_at",
                "last_activity_at",
            ]
        )
        command = _run_terminal_command(item)
        pid_file = _launch_command(
            command, title=item.task.terminal_title, state_key=state_key
        )
    except Exception as exc:
        item.mark_status(GitHubMonitorItem.Status.FAILED, failure_message=str(exc))
        notify_admins_of_failure(
            "Arthexis GitHub monitor launch failed",
            f"Failed to launch monitor item {item.pk} for {item.issue_url}: {exc}",
        )
        return {
            "launched": None,
            "reason": "launch_failed",
            "error": str(exc),
            "item": item.pk,
        }

    item.terminal_pid_file = str(pid_file)
    item.save(
        update_fields=[
            "terminal_pid_file",
        ]
    )
    return {"launched": item.pk, "reason": "launched", "pid_file": str(pid_file)}


def run_monitor_cycle(*, launch: bool = True) -> dict[str, Any]:
    if not github_monitor_enabled():
        return {"skipped": True, "reason": "feature_disabled"}
    if not GitHubMonitorTask.objects.filter(enabled=True).exists():
        return {"skipped": True, "reason": "not_configured"}

    lock_path = monitor_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(lock_path), timeout=0):
            return _run_monitor_cycle_unlocked(launch=launch)
    except Timeout:
        return {"skipped": True, "reason": "monitor_locked"}


def _run_monitor_cycle_unlocked(*, launch: bool = True) -> dict[str, Any]:
    """Run one monitor cycle while the per-device monitor lock is held."""

    with transaction.atomic():
        sync = sync_monitor_items()
        active = maintain_active_terminal()
        should_launch = launch and active.get("active") is None

    launch_result = {"launched": None, "reason": "launch_disabled"}
    if should_launch:
        launch_result = launch_next_queued_item()

    return {
        "skipped": False,
        "sync": sync,
        "active": active,
        "launch": launch_result,
    }


def record_activity(
    *, item_id: int | None = None, fingerprint: str = ""
) -> GitHubMonitorItem:
    item = _resolve_item(item_id=item_id, fingerprint=fingerprint)
    item.touch_activity()
    return item


def complete_item(
    *, item_id: int | None = None, fingerprint: str = ""
) -> GitHubMonitorItem:
    item = _resolve_item(item_id=item_id, fingerprint=fingerprint)
    was_active = item.status == GitHubMonitorItem.Status.ACTIVE
    pid_file = Path(item.terminal_pid_file) if item.terminal_pid_file else None
    if was_active and pid_file and _terminal_running(pid_file):
        if not _terminate_terminal(pid_file):
            raise RuntimeError("Active monitor terminal did not exit.")
    item.mark_status(GitHubMonitorItem.Status.COMPLETED)
    return item


def dismiss_item(
    *, item_id: int | None = None, fingerprint: str = ""
) -> GitHubMonitorItem:
    item = _resolve_item(item_id=item_id, fingerprint=fingerprint)
    was_active = item.status == GitHubMonitorItem.Status.ACTIVE
    pid_file = Path(item.terminal_pid_file) if item.terminal_pid_file else None
    if was_active and pid_file and _terminal_running(pid_file):
        if not _terminate_terminal(pid_file):
            raise RuntimeError("Active monitor terminal did not exit.")
    item.mark_status(GitHubMonitorItem.Status.DISMISSED)
    return item


def requeue_item(
    *, item_id: int | None = None, fingerprint: str = ""
) -> GitHubMonitorItem:
    item = _resolve_item(item_id=item_id, fingerprint=fingerprint)
    pid_file = Path(item.terminal_pid_file) if item.terminal_pid_file else None
    if item.status == GitHubMonitorItem.Status.ACTIVE and pid_file:
        if _terminal_running(pid_file) and not _terminate_terminal(pid_file):
            raise RuntimeError("Active monitor terminal did not exit.")
    now = timezone.now()
    item.status = GitHubMonitorItem.Status.QUEUED
    item.queued_at = now
    item.launched_at = None
    item.last_activity_at = None
    item.completed_at = None
    item.failure_message = ""
    item.prompt = ""
    item.terminal_state_key = ""
    item.terminal_pid_file = ""
    item.save(
        update_fields=[
            "status",
            "queued_at",
            "launched_at",
            "last_activity_at",
            "completed_at",
            "failure_message",
            "prompt",
            "terminal_state_key",
            "terminal_pid_file",
        ]
    )
    return item


def prompt_for_item(
    *, item_id: int | None = None, fingerprint: str = ""
) -> dict[str, Any]:
    item = _resolve_item(item_id=item_id, fingerprint=fingerprint)
    prompt = item.prompt or build_monitor_prompt(item)
    return {"item": item.pk, "fingerprint": item.fingerprint, "prompt": prompt}


def _resolve_item(
    *, item_id: int | None = None, fingerprint: str = ""
) -> GitHubMonitorItem:
    queryset = GitHubMonitorItem.objects.select_related("task", "task__repository")
    if item_id is not None:
        return queryset.get(pk=item_id)
    cleaned = fingerprint.strip()
    if cleaned:
        return queryset.get(fingerprint=cleaned)
    raise GitHubMonitorItem.DoesNotExist("Pass --item or --fingerprint.")


def notify_admins_of_failure(subject: str, body: str) -> bool:
    recipients, _ = resolve_recipient_fallbacks([], owner=None)
    if not recipients:
        logger.warning("GitHub monitor failure email skipped: no admin recipients")
        return False
    if not mailer.can_send_email():
        logger.warning("GitHub monitor failure email skipped: no email backend")
        return False
    try:
        node = Node.get_local()
    except Exception:
        node = None
    try:
        if node is not None:
            node.send_mail(subject, body, recipients)
        else:
            mailer.send(subject=subject, message=body, recipient_list=recipients)
    except Exception:
        logger.exception("GitHub monitor failure email failed")
        return False
    return True
