"""Models for GitHub-driven operator monitoring."""

from __future__ import annotations

from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.entity import Entity


class GitHubMonitorTask(Entity):
    """Configuration for one GitHub issue or PR signal that can launch work."""

    class TargetType(models.TextChoices):
        ISSUE = "issue", _("Issue")
        PULL_REQUEST = "pull_request", _("Pull request")

    name = models.SlugField(max_length=120, unique=True)
    display = models.CharField(max_length=160)
    repository = models.ForeignKey(
        "repos.GitHubRepository",
        related_name="monitor_tasks",
        on_delete=models.CASCADE,
    )
    enabled = models.BooleanField(default=True)
    target_type = models.CharField(
        max_length=32,
        choices=TargetType.choices,
        default=TargetType.ISSUE,
    )
    issue_title = models.CharField(max_length=255, blank=True)
    issue_marker = models.CharField(
        max_length=255,
        blank=True,
        help_text=_("Optional marker that must be present in the issue body."),
    )
    label_filter = models.CharField(
        max_length=120,
        blank=True,
        help_text=_("Optional label that must be present on the issue or PR."),
    )
    require_approval_reaction = models.BooleanField(default=False)
    approval_actor = models.CharField(max_length=120, blank=True)
    approval_emoji = models.CharField(max_length=32, blank=True, default="+1")
    terminal_title = models.CharField(max_length=160, default="Arthexis GitHub Monitor")
    terminal_state_key = models.SlugField(max_length=120, unique=True)
    codex_command = models.CharField(max_length=255, default="codex")
    prompt_template = models.TextField(blank=True)
    skill_slugs = models.JSONField(default=list, blank=True)
    inactivity_timeout_minutes = models.PositiveSmallIntegerField(default=45)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = _("GitHub Monitor Task")
        verbose_name_plural = _("GitHub Monitor Tasks")

    def __str__(self) -> str:
        return self.display or self.name

    @property
    def inactivity_timeout(self) -> timedelta:
        return timedelta(minutes=max(int(self.inactivity_timeout_minutes or 1), 1))


class GitHubMonitorItem(Entity):
    """One detected GitHub issue or PR waiting for an operator terminal."""

    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        ACTIVE = "active", _("Active")
        COMPLETED = "completed", _("Completed")
        CLOSED = "closed", _("Closed")
        TIMED_OUT = "timed_out", _("Timed out")
        FAILED = "failed", _("Failed")
        DISMISSED = "dismissed", _("Dismissed")

    task = models.ForeignKey(
        GitHubMonitorTask,
        related_name="items",
        on_delete=models.CASCADE,
    )
    fingerprint = models.CharField(max_length=64, unique=True)
    target_type = models.CharField(
        max_length=32,
        choices=GitHubMonitorTask.TargetType.choices,
        default=GitHubMonitorTask.TargetType.ISSUE,
    )
    issue_number = models.PositiveIntegerField()
    issue_title = models.CharField(max_length=500)
    issue_url = models.URLField(max_length=500, blank=True)
    issue_state = models.CharField(max_length=50, default="open")
    issue_body = models.TextField(blank=True)
    target_head_sha = models.CharField(max_length=64, blank=True)
    approved_by = models.CharField(max_length=120, blank=True)
    approval_emoji = models.CharField(max_length=32, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_head_sha = models.CharField(max_length=64, blank=True)
    prompt = models.TextField(blank=True)
    terminal_state_key = models.SlugField(max_length=120, blank=True)
    terminal_pid_file = models.CharField(max_length=500, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    queued_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    launched_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    failure_message = models.TextField(blank=True)

    class Meta:
        ordering = ("queued_at", "id")
        verbose_name = _("GitHub Monitor Item")
        verbose_name_plural = _("GitHub Monitor Items")
        indexes = [
            models.Index(fields=("status", "queued_at"), name="repos_ghmon_status_idx"),
            models.Index(fields=("task", "issue_number"), name="repos_ghmon_issue_idx"),
            models.Index(
                fields=("target_type", "status"),
                name="repos_ghmon_target_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("task", "issue_number"),
                name="unique_github_monitor_item_issue",
            )
        ]

    def __str__(self) -> str:
        return f"{self.task.name} #{self.issue_number} [{self.status}]"

    def mark_status(self, status: str, *, failure_message: str = "") -> None:
        self.status = status
        if failure_message:
            self.failure_message = failure_message
        if status in {
            self.Status.COMPLETED,
            self.Status.CLOSED,
            self.Status.TIMED_OUT,
            self.Status.FAILED,
            self.Status.DISMISSED,
        }:
            self.completed_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "failure_message",
                "completed_at",
            ]
        )

    def touch_activity(self, now=None) -> None:
        self.last_activity_at = now or timezone.now()
        self.save(update_fields=["last_activity_at"])
