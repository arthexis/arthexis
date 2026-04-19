"""Models for operation screen definitions and execution logs."""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest, Now
from django.utils import timezone
from django.utils.http import escape_leading_slashes
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity

logger = logging.getLogger(__name__)

VALIDATION_SQL_DISABLED_MESSAGE = _("Custom SQL validation is disabled for security reasons.")


def _sanitize_remediation_url(remediation_url: str) -> str:
    """Return a safe remediation URL for security alert links."""

    candidate = remediation_url.strip()
    if not candidate:
        return "/admin/"

    parsed = urlparse(candidate)
    if not parsed.scheme:
        if candidate.startswith("//"):
            return "/admin/"
        return candidate

    if parsed.scheme in {"http", "https"}:
        return candidate

    return "/admin/"


def validate_local_absolute_path_url(start_url: str) -> None:
    """Validate that start URLs are local absolute paths only."""

    parts = urlsplit(start_url or "")
    if parts.scheme or parts.netloc:
        raise ValidationError(_("Start URL must be a local absolute path."))

    path = escape_leading_slashes(parts.path)
    if not path.startswith("/"):
        raise ValidationError(_("Start URL must start with '/' and include no scheme or host."))


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
        help_text=_("Local absolute path where this operation starts, without scheme or host."),
        validators=[validate_local_absolute_path_url],
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
        validate_local_absolute_path_url(self.start_url)

    def run_validation_sql(self) -> tuple[bool | None, str]:
        """Return SQL validation status.

        Free-form SQL execution is intentionally disabled to prevent arbitrary database
        access through operation configuration.
        """

        sql = (self.validation_sql or "").strip()
        if not sql:
            return None, ""

        logger.warning("Blocked validation_sql execution for operation %s", self.pk)
        return None, _("Custom SQL validation is disabled for security reasons.")


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
            if passed is False:
                raise ValidationError({"validation_sql": output})
            self.validation_passed = passed
            self.validation_output = output
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return execution string summary."""

        return f"{self.operation} @ {self.performed_at:%Y-%m-%d %H:%M}"


class SecurityAlertEvent(Entity):
    """Aggregated operational error event used by the security alerts widget."""

    key = models.CharField(max_length=255, unique=True)
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
        indexes = [
            models.Index(
                fields=["is_active", "-last_occurred_at"],
                name="ops_secalert_active_last",
            )
        ]
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
    ) -> SecurityAlertEvent:
        """Create or update an event entry while incrementing occurrence metadata."""

        event_timestamp = occurred_at or timezone.now()
        safe_remediation_url = _sanitize_remediation_url(remediation_url)
        try:
            with transaction.atomic():
                return cls.objects.create(
                    key=key,
                    severity=severity,
                    message=message,
                    detail=detail,
                    occurrence_count=1,
                    last_occurred_at=event_timestamp,
                    remediation_url=safe_remediation_url,
                    is_active=True,
                )
        except IntegrityError:
            cls.objects.filter(key=key).update(
                severity=severity,
                message=message,
                detail=detail,
                remediation_url=safe_remediation_url,
                occurrence_count=F("occurrence_count") + 1,
                last_occurred_at=Greatest(F("last_occurred_at"), Value(event_timestamp)),
                is_active=True,
                updated_at=Now(),
            )
        return cls.objects.get(key=key)

    @classmethod
    def clear_occurrence(cls, *, key: str, occurred_at=None) -> None:
        """Mark an aggregated event inactive when the source recovers."""

        update_kwargs = {"is_active": False, "updated_at": Now()}
        if occurred_at is not None:
            update_kwargs["last_occurred_at"] = Greatest(
                F("last_occurred_at"),
                Value(occurred_at),
            )
        cls.objects.filter(key=key, is_active=True).update(**update_kwargs)


class OperatorJourney(Entity):
    """Linear guided workflow assigned to members of a security group."""

    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    security_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.CASCADE,
        related_name="operator_journeys",
    )
    priority = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("priority", "name")
        verbose_name = _("Operator Journey")
        verbose_name_plural = _("Operator Journeys")

    def __str__(self) -> str:
        """Return journey label for admin and logs."""

        return self.name


class OperatorJourneyStep(Entity):
    """Single required manual step inside a linear operator journey."""

    journey = models.ForeignKey(
        OperatorJourney,
        on_delete=models.CASCADE,
        related_name="steps",
    )
    title = models.CharField(max_length=160)
    slug = models.SlugField()
    instruction = models.TextField(
        help_text=_("Operator-facing guidance shown above the embedded frame."),
    )
    help_text = models.TextField(
        blank=True,
        help_text=_("Optional extra help for manual actions required outside Arthexis."),
    )
    iframe_url = models.CharField(
        max_length=500,
        validators=[validate_local_absolute_path_url],
        help_text=_("Local absolute path rendered in the embedded frame."),
    )
    order = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("journey__priority", "journey__name", "order", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("journey", "slug"),
                name="ops_operatorjourneystep_unique_slug_per_journey",
            ),
            models.UniqueConstraint(
                fields=("journey", "order"),
                name="ops_operatorjourneystep_unique_order_per_journey",
            ),
        ]
        verbose_name = _("Operator Journey Step")
        verbose_name_plural = _("Operator Journey Steps")

    def __str__(self) -> str:
        """Return step label for admin and logs."""

        return f"{self.journey}: {self.title}"

    def clean(self) -> None:
        """Validate iframe URL stays local to this node."""

        super().clean()
        validate_local_absolute_path_url(self.iframe_url)


class OperatorJourneyStepCompletion(Entity):
    """Per-user completion marker for operator journey steps."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="operator_journey_step_completions",
    )
    step = models.ForeignKey(
        OperatorJourneyStep,
        on_delete=models.CASCADE,
        related_name="completions",
    )
    completed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("-completed_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("user", "step"),
                name="ops_operatorjourneystepcompletion_unique_user_step",
            )
        ]
        verbose_name = _("Operator Journey Step Completion")
        verbose_name_plural = _("Operator Journey Step Completions")

    def __str__(self) -> str:
        """Return concise completion label."""

        return f"{self.user} completed {self.step}"
