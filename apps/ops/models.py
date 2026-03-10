"""Models for operation screen definitions and execution logs."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection, models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


logger = logging.getLogger(__name__)


READ_ONLY_SQL_PATTERN = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class OperationScreen(Entity):
    """Defines an operation that staff can execute through one or more screens."""

    class Scope(models.TextChoices):
        PER_USER = "per_user", _("Once per user")
        PER_NODE = "per_node", _("Once per node")

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField()
    start_url = models.CharField(
        max_length=500,
        help_text=_("Internal admin or public URL where this operation starts."),
    )
    validation_sql = models.TextField(
        blank=True,
        help_text=_("Optional SQL query returning a truthy scalar when validation passes."),
    )
    priority = models.PositiveIntegerField(default=100)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_operations",
    )
    scope = models.CharField(max_length=20, choices=Scope.choices, default=Scope.PER_USER)
    recurrence_days = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("Number of days after completion when this operation becomes pending again."),
    )
    is_required = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("priority", "title")
        verbose_name = _("Operation Screen")
        verbose_name_plural = _("Operation Screens")

    def __str__(self) -> str:
        """Return human-readable operation title."""

        return self.title

    def clean(self) -> None:
        """Validate recurrence semantics for operation scope."""

        super().clean()
        if self.recurrence_days is not None and self.recurrence_days < 1:
            raise ValidationError({"recurrence_days": _("Recurrence must be at least one day.")})
        scheme = urlparse(self.start_url).scheme
        if scheme and scheme not in {"http", "https"}:
            raise ValidationError({"start_url": _("Start URL must be HTTP(S) or a relative path.")})

    def run_validation_sql(self) -> tuple[bool | None, str]:
        """Execute optional SQL validation and return pass flag with output."""

        sql = (self.validation_sql or "").strip()
        if not sql:
            return None, ""
        if not READ_ONLY_SQL_PATTERN.match(sql):
            return False, _("Validation SQL must be a read-only SELECT query.")

        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    row = cursor.fetchone()
                transaction.set_rollback(True)
        except DatabaseError as exc:
            logger.exception("Validation SQL failed for operation %s", self.pk)
            return False, str(exc)

        if not row:
            return False, _("Validation query returned no rows.")

        return bool(row[0]), str(row[0])


class OperationLink(Entity):
    """Supplemental links shown with an operation definition."""

    operation = models.ForeignKey(
        OperationScreen,
        on_delete=models.CASCADE,
        related_name="links",
    )
    label = models.CharField(max_length=120)
    url = models.URLField()
    priority = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("priority", "id")
        verbose_name = _("Operation Link")
        verbose_name_plural = _("Operation Links")

    def __str__(self) -> str:
        """Return label for admin display."""

        return self.label


class OperationExecution(Entity):
    """Stores completion logs for operations and recurrence notifications."""

    operation = models.ForeignKey(
        OperationScreen,
        on_delete=models.CASCADE,
        related_name="executions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="operation_executions",
    )
    node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operation_executions",
    )
    performed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)
    validation_passed = models.BooleanField(null=True, blank=True)
    validation_output = models.TextField(blank=True)
    expiration_notified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-performed_at",)
        verbose_name = _("Operation Execution")
        verbose_name_plural = _("Operation Executions")
        indexes = [models.Index(fields=["operation", "user", "-performed_at"], name="ops_exec_op_user_date")]

    def save(self, *args, **kwargs):
        """Run optional operation SQL validation when logging execution."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None and not {"validation_passed", "validation_output"}.intersection(
            set(update_fields)
        ):
            super().save(*args, **kwargs)
            return
        if self.validation_passed is None:
            passed, output = self.operation.run_validation_sql()
            self.validation_passed = passed
            self.validation_output = output
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return execution string summary."""

        return f"{self.operation} @ {self.performed_at:%Y-%m-%d %H:%M}"


class SecurityAlertEvent(Entity):
    """Aggregated operational error event used by the security alerts widget."""

    key = models.CharField(max_length=120, unique=True)
    severity = models.CharField(max_length=20, default="error")
    message = models.CharField(max_length=255)
    detail = models.TextField(blank=True)
    occurrence_count = models.PositiveIntegerField(default=1)
    last_occurred_at = models.DateTimeField(default=timezone.now)
    remediation_url = models.CharField(max_length=500, default="/admin/")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_occurred_at", "-updated_at")
        verbose_name = _("Security Alert Event")
        verbose_name_plural = _("Security Alert Events")

    def __str__(self) -> str:
        """Return a readable key/message label for admin lists."""

        return f"{self.key}: {self.message}"

    @classmethod
    def record_occurrence(
        cls,
        *,
        key: str,
        message: str,
        detail: str = "",
        severity: str = "error",
        remediation_url: str = "/admin/",
        occurred_at=None,
    ) -> "SecurityAlertEvent":
        """Create or update an event entry while incrementing occurrence metadata."""

        event, created = cls.objects.get_or_create(
            key=key,
            defaults={
                "severity": severity,
                "message": message,
                "detail": detail,
                "occurrence_count": 1,
                "last_occurred_at": occurred_at or timezone.now(),
                "remediation_url": remediation_url,
                "is_active": True,
            },
        )
        if created:
            return event

        event.severity = severity
        event.message = message
        event.detail = detail
        event.remediation_url = remediation_url
        event.occurrence_count += 1
        event.last_occurred_at = occurred_at or timezone.now()
        event.is_active = True
        event.save(
            update_fields=[
                "severity",
                "message",
                "detail",
                "remediation_url",
                "occurrence_count",
                "last_occurred_at",
                "is_active",
                "updated_at",
            ]
        )
        return event


@dataclass(slots=True)
class PendingOperation:
    """Computed pending operation context for UI display."""

    operation: OperationScreen
    latest_execution: OperationExecution | None


def _latest_execution_for_user(operation: OperationScreen, user, *, node=None) -> OperationExecution | None:
    queryset = operation.executions.filter(user=user)
    if operation.scope == OperationScreen.Scope.PER_NODE:
        queryset = queryset.filter(node=node)
    return queryset.order_by("-performed_at").first()


def _is_expired(operation: OperationScreen, execution: OperationExecution | None) -> bool:
    if execution is None:
        return True
    if not operation.recurrence_days:
        return False
    expires_at = execution.performed_at + timedelta(days=operation.recurrence_days)
    return timezone.now() >= expires_at


def pending_operations_for_user(user, *, node=None, required_only: bool = False) -> list[PendingOperation]:
    """Return operations currently pending for a user."""

    if not getattr(user, "is_authenticated", False):
        return []

    operations = OperationScreen.objects.filter(is_active=True)
    if required_only:
        operations = operations.filter(is_required=True)

    pending: list[PendingOperation] = []
    for operation in operations.order_by("priority", "id"):
        latest = _latest_execution_for_user(operation, user, node=node)
        if _is_expired(operation, latest):
            pending.append(PendingOperation(operation=operation, latest_execution=latest))
    return pending
