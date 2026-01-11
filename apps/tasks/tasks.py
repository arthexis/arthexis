from __future__ import annotations

import logging
import shlex
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests
from celery import shared_task
from django.apps import apps as django_apps
from django.conf import settings
from django.db import models
from django.utils import timezone

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


@shared_task(name="apps.content.tasks.run_scheduled_web_samplers")
def run_scheduled_web_samplers() -> list[int]:
    """Execute any web request samplers that are due."""

    from apps.content.web_sampling import schedule_pending_samplers

    executed = schedule_pending_samplers()
    if executed:
        logger.info("Executed %s scheduled web samplers", len(executed))
    return executed


@shared_task(name="apps.repos.tasks.report_exception_to_github")
def report_exception_to_github(payload: dict[str, Any]) -> None:
    """Send exception context to the GitHub issue helper.

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
        logger.info(
            "Created GitHub issue %s for user story %s", issue_url, story.pk
        )
    else:
        logger.info(
            "No GitHub issue created for user story %s", story.pk
        )

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
    def __init__(
        self, *, command: str | None = None, timeout: int = DEFAULT_PROMPT_TIMEOUT
    ):
        self.command = command
        self.timeout = timeout

    def summarize(self, prompt: str) -> str:
        if not self.command:
            return self._fallback(prompt)
        try:
            result = subprocess.run(
                shlex.split(self.command),
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
                shell=False,
            )
        except Exception:
            logger.exception("Failed to run local LLM command")
            return self._fallback(prompt)
        if result.returncode != 0:
            logger.warning("Local LLM command returned %s", result.returncode)
            return self._fallback(prompt)
        return result.stdout.strip()

    def _fallback(self, prompt: str) -> str:
        log_lines: list[str] = []
        in_logs = False
        for line in prompt.splitlines():
            if line.strip() == "LOGS:":
                in_logs = True
                continue
            if in_logs and line.strip():
                log_lines.append(line)
        sample = log_lines[-20:] if log_lines else [line for line in prompt.splitlines() if line]
        summary = []
        for idx in range(0, min(len(sample), 20), 2):
            subject = f"LOG {idx // 2 + 1}"
            body = sample[idx][:16]
            summary.append(subject)
            summary.append(body)
            summary.append("---")
        return "\n".join(summary)


def _write_lcd_frames(
    frames: list[tuple[str, str]],
    *,
    lock_file: Path,
    sleep_seconds: int = DEFAULT_SLEEP_SECONDS,
    sleep_fn=time.sleep,
) -> None:
    from apps.summary.services import render_lcd_payload

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    for subject, body in frames:
        payload = render_lcd_payload(subject, body)
        lock_file.write_text(payload, encoding="utf-8")
        sleep_fn(sleep_seconds)


@shared_task(name="summary.tasks.generate_lcd_log_summary")
def generate_lcd_log_summary() -> str:
    from apps.nodes.models import Node
    from apps.screens.startup_notifications import LCD_LOW_LOCK_FILE
    from apps.summary.services import (
        build_summary_prompt,
        collect_recent_logs,
        compact_log_chunks,
        ensure_local_model,
        get_summary_config,
        normalize_screens,
        parse_screens,
    )

    node = Node.get_local()
    if not node:
        return "skipped:no-node"

    if not node.has_feature("llm-summary"):
        return "skipped:feature-disabled"

    config = get_summary_config()
    if not config.is_active:
        return "skipped:inactive"

    ensure_local_model(config)

    now = timezone.now()
    since = config.last_run_at or (now - timedelta(minutes=5))
    chunks = collect_recent_logs(config, since=since)
    compacted_logs = compact_log_chunks(chunks)
    if not compacted_logs:
        config.last_run_at = now
        config.save(update_fields=["last_run_at", "log_offsets", "model_path", "installed_at", "updated_at"])
        return "skipped:no-logs"

    prompt = build_summary_prompt(compacted_logs, now=now)
    command = config.model_command or getattr(settings, "LLM_SUMMARY_COMMAND", "") or None
    summarizer = LocalLLMSummarizer(
        command=command,
        timeout=getattr(settings, "LLM_SUMMARY_TIMEOUT", DEFAULT_PROMPT_TIMEOUT),
    )
    output = summarizer.summarize(prompt)
    screens = normalize_screens(parse_screens(output))

    if not screens:
        screens = normalize_screens([("No events", "-"), ("Chk logs", "manual")])

    lock_file = Path(settings.BASE_DIR) / ".locks" / LCD_LOW_LOCK_FILE
    _write_lcd_frames(screens[:10], lock_file=lock_file)

    config.last_run_at = now
    config.last_prompt = prompt
    config.last_output = output
    config.save(
        update_fields=[
            "last_run_at",
            "last_prompt",
            "last_output",
            "log_offsets",
            "model_path",
            "installed_at",
            "updated_at",
        ]
    )
    return f"wrote:{len(screens)}"
