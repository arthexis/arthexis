"""GitHub issue monitoring that launches one operator terminal at a time."""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil
from django.conf import settings
from django.db import transaction
from django.utils import timezone
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


GITHUB_MONITOR_FEATURE_FIELDS = {
    "display": "GitHub Monitoring",
    "source": Feature.Source.MAINSTREAM,
    "summary": (
        "Monitors configured GitHub readiness issues and launches one local "
        "operator terminal at a time."
    ),
    "admin_requirements": (
        "Operators can enable the feature, configure monitored issue signals, "
        "review queued monitor items, and install default issue/PR policy skills."
    ),
    "public_requirements": "No public interface; this is local operator automation.",
    "service_requirements": (
        "The scheduled monitor task must do nothing until monitor task rows exist. "
        "When configured, it polls GitHub issues, maintains one active terminal, "
        "times out inactive consoles, and emails admins on failures."
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

Before work and after material progress, update activity:
{heartbeat_command}

When this monitor item no longer needs an active console, mark it complete:
{complete_command}

Current issue body:
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
        task.issue_title = spec.issue_title
        task.issue_marker = spec.issue_marker
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
        "tasks": task_summaries,
    }


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

    return {
        "feature_enabled": feature_enabled,
        "token_configured": token_configured,
        "token_error": token_error,
        "desktop_ui_enabled": desktop_enabled,
        "celery_lock_enabled": celery_lock_enabled(),
        "email_configured": mailer.can_send_email(),
        "configured_tasks": enabled_tasks,
        "active_items": active_items,
        "queued_items": queued_items,
        "ready": bool(
            feature_enabled and token_configured and desktop_enabled and enabled_tasks
        ),
    }


def _issue_matches(task: GitHubMonitorTask, item: Mapping[str, object]) -> bool:
    if "pull_request" in item:
        return False
    if str(item.get("title") or "").strip() != task.issue_title:
        return False
    marker = (task.issue_marker or "").strip()
    if marker and marker not in str(item.get("body") or ""):
        return False
    return isinstance(item.get("number"), int)


def _fingerprint(task: GitHubMonitorTask, issue_number: int) -> str:
    source = f"{task.repository.slug}|{task.name}|{issue_number}"
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
) -> GitHubMonitorItem:
    issue_number = int(issue["number"])
    fingerprint = _fingerprint(task, issue_number)
    defaults = {
        "issue_title": str(issue.get("title") or ""),
        "issue_url": str(issue.get("html_url") or ""),
        "issue_state": str(issue.get("state") or "open"),
        "issue_body": _issue_body(issue),
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


def sync_monitor_items(*, token: str | None = None, now=None) -> dict[str, Any]:
    now = now or timezone.now()
    token = token or github_service.get_github_issue_token()
    created_or_seen = []
    matched_fingerprints: dict[int, set[str]] = {}
    tasks = GitHubMonitorTask.objects.select_related("repository").filter(enabled=True)

    for task in tasks:
        seen_for_task: set[str] = set()
        for issue in github_service.fetch_repository_issues(
            token=token,
            owner=task.repository.owner,
            name=task.repository.name,
        ):
            if not _issue_matches(task, issue):
                continue
            item = _upsert_monitor_item(task, issue, now=now)
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
        "policy_skills": _policy_skill_lines(item),
        "heartbeat_command": _manage_command("heartbeat", item),
        "complete_command": _manage_command("complete", item),
        "inactivity_timeout_minutes": item.task.inactivity_timeout_minutes,
    }
    return template.format_map(values)


def _split_codex_command(command: str) -> list[str]:
    command = command.strip() or "codex"
    return [part.strip('"') for part in shlex.split(command, posix=os.name != "nt")]


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
        command = [*_split_codex_command(item.task.codex_command), prompt]
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

    item.status = GitHubMonitorItem.Status.ACTIVE
    item.prompt = prompt
    item.terminal_state_key = state_key
    item.terminal_pid_file = str(pid_file)
    item.launched_at = now
    item.last_activity_at = now
    item.save(
        update_fields=[
            "status",
            "prompt",
            "terminal_state_key",
            "terminal_pid_file",
            "launched_at",
            "last_activity_at",
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
        launch_result = {"launched": None, "reason": "launch_disabled"}
        if launch and active.get("active") is None:
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
