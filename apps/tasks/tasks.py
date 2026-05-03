from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from celery import shared_task
from django.apps import apps as django_apps
from django.db import models
from django.utils import timezone

from apps.summary.constants import LLM_SUMMARY_CELERY_TASK_NAME

logger = logging.getLogger(__name__)

DEFAULT_SLEEP_SECONDS = 30
DEFAULT_PROMPT_TIMEOUT = 240


@shared_task
def send_manual_task_notification(manual_task_id: int, trigger: str) -> None:
    """Send reminder emails for the given manual task."""

    from apps.tasks.models import ManualTaskRequest

    task = ManualTaskRequest.objects.filter(pk=manual_task_id).first()
    if task is None:
        logger.debug(
            "ManualTask notification skipped; task %s not found", manual_task_id
        )
        return
    if not task.enable_notifications:
        logger.debug(
            "ManualTask notification skipped; notifications disabled for %s",
            manual_task_id,
        )
        return
    try:
        sent = task.send_notification_email(trigger)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception(
            "ManualTask notification failed for %s using trigger %s",
            manual_task_id,
            trigger,
        )
        return
    if not sent:
        logger.debug(
            "ManualTask notification skipped; no recipients for %s",
            manual_task_id,
        )


@shared_task(name="apps.tasks.tasks.create_manual_task_github_issue")
def create_manual_task_github_issue(manual_task_id: int, trigger: str) -> str | None:
    """Create a GitHub issue for a manual task when ``trigger`` is eligible."""

    from apps.repos.services.github import GitHubRepositoryError
    from apps.tasks.models import ManualTaskRequest

    task = ManualTaskRequest.objects.filter(pk=manual_task_id).first()
    if task is None:
        logger.debug(
            "Manual task GitHub issue skipped; task %s not found", manual_task_id
        )
        return None
    if not task.can_open_github_issue_for_trigger(trigger):
        logger.debug(
            "Manual task GitHub issue skipped; trigger %s is not eligible for %s",
            trigger,
            manual_task_id,
        )
        return task.github_issue_url or None
    try:
        issue_url = task.create_github_issue()
    except GitHubRepositoryError as exc:
        logger.warning(
            "Manual task GitHub issue failed for %s using trigger %s: %s",
            manual_task_id,
            trigger,
            exc,
        )
        return None
    except Exception:  # pragma: no cover - defensive logging
        logger.exception(
            "Unexpected error while creating manual task GitHub issue for %s",
            manual_task_id,
        )
        return None

    if issue_url:
        logger.info(
            "Created GitHub issue %s for manual task %s using trigger %s",
            issue_url,
            manual_task_id,
            trigger,
        )
    return issue_url


@shared_task(name="apps.repos.tasks.report_exception_to_github")
def report_exception_to_github(payload: dict[str, Any]) -> None:
    """Send exception context to the GitHub issue helper.

    Parameters:
        payload: Serialized request exception data queued by the repos app.

    Returns:
        ``None``.

    The task is intentionally light-weight in this repository. Deployments can
    replace it with an implementation that forwards ``payload`` to the
    automation responsible for creating GitHub issues.
    """

    logger.info(
        "Queued GitHub issue report for %s", payload.get("fingerprint", "<unknown>")
    )


@shared_task(name="apps.sites.tasks.create_user_story_github_issue")
def create_user_story_github_issue(user_story_id: int) -> str | None:
    """Create a GitHub issue for the provided ``UserStory`` instance."""

    from apps.sites.models import UserStory

    try:
        story = UserStory.objects.get(pk=user_story_id)
    except UserStory.DoesNotExist:  # pragma: no cover - defensive guard
        logger.warning(
            "User story %s no longer exists; skipping GitHub issue creation",
            user_story_id,
        )
        return None

    if story.rating >= 5:
        logger.info(
            "Skipping GitHub issue creation for user story %s with rating %s",
            story.pk,
            story.rating,
        )
        return None

    if story.github_issue_url:
        logger.info(
            "GitHub issue already recorded for user story %s: %s",
            story.pk,
            story.github_issue_url,
        )
        return story.github_issue_url

    issue_url = story.create_github_issue()

    if issue_url:
        logger.info("Created GitHub issue %s for user story %s", issue_url, story.pk)
    else:
        logger.info("No GitHub issue created for user story %s", story.pk)

    return issue_url


@shared_task(name="apps.sites.tasks.purge_leads")
def purge_leads(days: int = 30) -> int:
    """Remove lead records older than ``days`` days."""

    from apps.leads.models import Lead

    cutoff = timezone.now() - timedelta(days=days)
    total_deleted = 0

    lead_models = [
        model
        for model in django_apps.get_models()
        if issubclass(model, Lead)
        and not model._meta.abstract
        and not model._meta.proxy
    ]

    for model in sorted(lead_models, key=lambda item: item._meta.label):
        deleted, _ = model.objects.filter(created_on__lt=cutoff).delete()
        total_deleted += deleted

    if total_deleted:
        logger.info("Purged %s leads older than %s days", total_deleted, days)
    return total_deleted


@shared_task(name="apps.links.tasks.validate_reference_links")
def validate_reference_links() -> int:
    """Validate stale or missing reference URLs and store their status codes."""

    from apps.links.models import Reference

    now = timezone.now()
    cutoff = now - timedelta(days=7)
    references = Reference.objects.filter(
        models.Q(validated_url_at__isnull=True) | models.Q(validated_url_at__lt=cutoff)
    ).exclude(value="")

    updated = 0
    for reference in references:
        status_code: int | None = None
        try:
            response = requests.get(reference.value, timeout=5)
        except requests.RequestException as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            logger.warning(
                "Failed to validate reference %s at %s", reference.pk, reference.value
            )
            logger.debug("Reference validation error", exc_info=exc)
        else:
            status_code = response.status_code

        reference.validation_status = status_code if status_code is not None else 0
        reference.validated_url_at = now
        reference.save(update_fields=["validation_status", "validated_url_at"])
        updated += 1

    return updated


class LocalLLMSummarizer:
    """Deterministic in-process summarizer used for LCD log rotations."""

    def __init__(self) -> None:
        """Initialize the fixed summarizer adapter."""

    def summarize(self, prompt: str) -> str:
        """Return a deterministic LCD summary for ``prompt`` without subprocesses."""

        return self._fallback(prompt)

    def _fallback(self, prompt: str) -> str:
        """Build dense, repeatable LCD screens from compacted prompt log lines."""
        log_lines: list[str] = []
        in_logs = False
        for line in prompt.splitlines():
            if line.strip() == "LOGS:":
                in_logs = True
                continue
            if in_logs and line.strip():
                log_lines.append(line)

        event_lines = [
            line
            for line in log_lines
            if not (line.startswith("[") and line.endswith("]"))
        ]
        if not event_lines:
            return "Quiet\nNo new logs\n---"

        error_lines = [line for line in event_lines if _summary_severity(line) == "ERR"]
        warn_lines = [line for line in event_lines if _summary_severity(line) == "WRN"]
        task_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for line in event_lines:
            task_label = _summary_task_label(line)
            if task_label:
                task_counts[task_label] = task_counts.get(task_label, 0) + 1
                continue
            source_label = _summary_source_label(line)
            if source_label:
                source_counts[source_label] = source_counts.get(source_label, 0) + 1

        screens: list[tuple[str, str]] = []
        if error_lines or warn_lines:
            screens.append(
                (
                    f"ERR {len(error_lines)} WRN {len(warn_lines)}",
                    _summary_compact_line((error_lines or warn_lines)[-1]),
                )
            )
        else:
            screens.append(("OK no err/warn", f"{len(event_lines)} lines"))

        for line in (error_lines + warn_lines)[-3:]:
            screens.append((_summary_severity(line), _summary_compact_line(line)))

        for label, count in _summary_top_counts(task_counts, limit=4):
            screens.append((label, f"{count}x /5m"))

        if len(screens) < 3:
            for label, count in _summary_top_counts(source_counts, limit=3):
                screens.append((label, f"{count}x /5m"))

        if len(screens) == 1:
            screens.append(("Routine only", "No action"))

        return "\n---\n".join(f"{subject}\n{body}" for subject, body in screens)


SUMMARY_TASK_ALIASES = {
    "apps.core.tasks.heartbeat": "HB ok",
    "apps.ocpp.tasks.setup_forwarders": "OCPP fwd",
    "apps.ocpp.tasks.send_offline_charge_point_notifications": "OCPP note",
    "terminals.ensure_agent_terminals": "Term chk",
}

SUMMARY_SOURCE_ALIASES = {
    "apps.core.tasks.heartbeat": "HB",
    "celery.beat": "Beat",
    "celery.worker.strategy": "Worker",
    "celery.app.trace": "Task trace",
    "apps.ocpp": "OCPP",
}

SUMMARY_TASK_RE = re.compile(r"Task ([\w.]+)\[")
SUMMARY_DUE_TASK_RE = re.compile(r"Sending due task [\w-]+ \(([\w.]+)\)")
SUMMARY_SOURCE_RE = re.compile(r"^(?:DBG|INF|WRN|ERR|CRI)\s+([\w.]+):")


def _summary_severity(line: str) -> str:
    if line.startswith("ERR ") or line.startswith("CRI ") or " raised unexpected" in line:
        return "ERR"
    if line.startswith("WRN "):
        return "WRN"
    return "OK"


def _summary_alias(value: str, aliases: dict[str, str]) -> str:
    for prefix, label in aliases.items():
        if value == prefix or value.startswith(f"{prefix}."):
            return label
    return value.rsplit(".", 1)[-1].replace("_", " ")[:16]


def _summary_task_label(line: str) -> str | None:
    match = SUMMARY_DUE_TASK_RE.search(line) or SUMMARY_TASK_RE.search(line)
    if not match:
        if "Heartbeat task executed" in line:
            return "HB ok"
        return None
    return _summary_alias(match.group(1), SUMMARY_TASK_ALIASES)


def _summary_source_label(line: str) -> str | None:
    match = SUMMARY_SOURCE_RE.match(line)
    if not match:
        return None
    return _summary_alias(match.group(1), SUMMARY_SOURCE_ALIASES)


def _summary_top_counts(counts: dict[str, int], *, limit: int) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]


def _summary_compact_line(line: str) -> str:
    cleaned = re.sub(r"^(?:DBG|INF|WRN|ERR|CRI)\s+", "", line)
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    cleaned = cleaned.replace("Task ", "")
    cleaned = cleaned.replace("raised unexpected:", "raised:")
    cleaned = cleaned.replace("Scheduler: Sending due task", "due")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return "-"
    return cleaned[:16]


def _write_lcd_frames(
    frames: list[tuple[str, str]],
    *,
    lock_file: Path,
    expires_at=None,
) -> None:
    """Persist LCD frames into channel lock files.

    The base lock path stores frame zero and additional frames are written to
    ``<base>-<index>`` files so the LCD runner can rotate them as one channel.
    """

    from apps.summary.services import render_lcd_payload

    base_name = lock_file.name
    prefix = f"{base_name}-"

    if not frames:
        lock_file.unlink(missing_ok=True)
        for candidate in lock_file.parent.glob(f"{prefix}*"):
            suffix = candidate.name[len(prefix) :]
            if suffix.isdigit():
                candidate.unlink(missing_ok=True)
        return

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    for idx, (subject, body) in enumerate(frames):
        target = lock_file if idx == 0 else lock_file.with_name(f"{base_name}-{idx}")
        payload = render_lcd_payload(subject, body, expires_at=expires_at)
        target.write_text(payload, encoding="utf-8")

    for candidate in lock_file.parent.glob(f"{prefix}*"):
        suffix = candidate.name[len(prefix) :]
        if not suffix.isdigit() or (0 < int(suffix) < len(frames)):
            continue
        candidate.unlink(missing_ok=True)


@shared_task(name=LLM_SUMMARY_CELERY_TASK_NAME)
def generate_lcd_log_summary() -> str:
    from apps.summary.services import execute_log_summary_generation

    return execute_log_summary_generation()
