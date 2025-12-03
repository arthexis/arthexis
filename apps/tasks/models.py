from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal
from math import ceil
from typing import Iterator, Sequence

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from apps.celery.utils import is_celery_enabled
from apps.core.entity import Entity, EntityAllManager, EntityManager
from apps.core.models import User as CoreUser
from apps.groups.models import SecurityGroup as CoreSecurityGroup
from apps.emails import mailer
from apps.odoo.models import OdooProduct as CoreOdooProduct


logger = logging.getLogger(__name__)


class TaskCategoryManager(EntityManager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class TaskCategory(Entity):
    """Standardized categories for manual work assignments."""

    AVAILABILITY_NONE = "none"
    AVAILABILITY_IMMEDIATE = "immediate"
    AVAILABILITY_2_3_BUSINESS_DAYS = "2_3_business_days"
    AVAILABILITY_2_3_WEEKS = "2_3_weeks"
    AVAILABILITY_UNAVAILABLE = "unavailable"

    AVAILABILITY_CHOICES = [
        (AVAILABILITY_NONE, _("None")),
        (AVAILABILITY_IMMEDIATE, _("Immediate")),
        (AVAILABILITY_2_3_BUSINESS_DAYS, _("2-3 business days")),
        (AVAILABILITY_2_3_WEEKS, _("2-3 weeks")),
        (AVAILABILITY_UNAVAILABLE, _("Unavailable")),
    ]

    name = models.CharField(_("Name"), max_length=200)
    description = models.TextField(
        _("Description"),
        blank=True,
        help_text=_("Optional details supporting Markdown formatting."),
    )
    image = models.ImageField(
        _("Image"), upload_to="workgroup/task_categories/", blank=True
    )
    cost = models.DecimalField(
        _("Cost"),
        max_digits=10,
        decimal_places=2,
        blank=True,
        null=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=_("Estimated fulfillment cost in local currency."),
    )
    default_duration = models.DurationField(
        _("Default duration"),
        null=True,
        blank=True,
        help_text=_("Typical time expected to complete tasks in this category."),
    )
    manager = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_task_categories",
        verbose_name=_("Manager"),
        help_text=_("User responsible for overseeing this category."),
    )
    odoo_products = models.ManyToManyField(
        CoreOdooProduct,
        related_name="task_categories",
        verbose_name=_("Odoo products"),
        blank=True,
        help_text=_("Relevant Odoo products for this category."),
    )
    availability = models.CharField(
        _("Availability"),
        max_length=32,
        choices=AVAILABILITY_CHOICES,
        default=AVAILABILITY_NONE,
        help_text=_("Typical lead time for scheduling this work."),
    )
    requestor_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_requestor",
        verbose_name=_("Requestor group"),
        help_text=_("Security group authorized to request this work."),
    )
    assigned_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="task_categories_as_assignee",
        verbose_name=_("Assigned to group"),
        help_text=_("Security group typically assigned to this work."),
    )

    objects = TaskCategoryManager()
    all_objects = EntityAllManager()

    class Meta:
        verbose_name = _("Task Category")
        verbose_name_plural = _("Task Categories")
        ordering = ("name",)
        db_table = "core_taskcategory"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    natural_key.dependencies = []  # type: ignore[attr-defined]

    def availability_label(self) -> str:  # pragma: no cover - admin helper
        return self.get_availability_display()

    availability_label.short_description = _("Availability")  # type: ignore[attr-defined]


class ManualTask(Entity):
    """Manual work scheduled for nodes or locations."""

    description = models.TextField(
        _("Requestor Comments"),
        help_text=_("Detailed summary of the work to perform."),
    )
    category = models.ForeignKey(
        "tasks.TaskCategory",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Category"),
        help_text=_("Select the standardized category for this work."),
    )
    assigned_user = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_manual_tasks",
        verbose_name=_("Assigned user"),
        help_text=_("Optional user responsible for the task."),
    )
    assigned_group = models.ForeignKey(
        "groups.SecurityGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_manual_tasks",
        verbose_name=_("Potential assignees"),
        help_text=_("Security group containing users who can fulfill the task."),
    )
    manager = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_manual_tasks",
        verbose_name=_("Manager"),
        help_text=_("User overseeing the task."),
    )
    odoo_products = models.ManyToManyField(
        CoreOdooProduct,
        blank=True,
        related_name="manual_tasks",
        verbose_name=_("Odoo products"),
        help_text=_("Products associated with the requested work."),
    )
    duration = models.DurationField(
        _("Expected duration"),
        null=True,
        blank=True,
        help_text=_("Estimated time to complete the task."),
    )
    node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Node"),
        help_text=_("Node where this manual task should be completed."),
    )
    location = models.ForeignKey(
        "maps.Location",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Location"),
        help_text=_("Location associated with this manual task."),
    )
    scheduled_start = models.DateTimeField(
        _("Scheduled start"), help_text=_("Planned start time for this work."),
    )
    scheduled_end = models.DateTimeField(
        _("Scheduled end"), help_text=_("Planned completion time for this work."),
    )
    enable_notifications = models.BooleanField(
        _("Enable notifications"),
        default=False,
        help_text=_(
            "Send reminder emails to the assigned contacts when Celery notifications are available."
        ),
    )

    class Meta:
        verbose_name = _("Manual Task")
        verbose_name_plural = _("Manual Tasks")
        ordering = ("scheduled_start", "category__name")
        db_table = "core_manualtask"
        constraints = [
            models.CheckConstraint(
                name="manualtask_requires_target",
                condition=Q(node__isnull=False) | Q(location__isnull=False),
            ),
            models.CheckConstraint(
                name="manualtask_schedule_order",
                condition=Q(scheduled_end__gte=F("scheduled_start")),
            ),
        ]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.node and not self.location:
            message = _("Select at least one node or location.")
            errors["node"] = [message]
            errors["location"] = [message]
        if self.scheduled_start and self.scheduled_end:
            if self.scheduled_end < self.scheduled_start:
                errors.setdefault("scheduled_end", []).append(
                    _("Scheduled end must be on or after the scheduled start."),
                )
        if errors:
            raise ValidationError(errors)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.category:
            return self.category.name
        if self.description:
            return self.description[:50]
        return super().__str__()

    # Notification helpers -------------------------------------------

    def _iter_group_emails(self, group: CoreSecurityGroup | None) -> Iterator[str]:
        if not group or not group.pk:
            return
        queryset = group.user_set.filter(is_active=True).exclude(email="")
        for email in queryset.values_list("email", flat=True):
            normalized = (email or "").strip()
            if normalized:
                yield normalized

    def _iter_node_admin_emails(self) -> Iterator[str]:
        node = self.node
        if not node:
            return
        outbox = getattr(node, "email_outbox", None)
        if not outbox:
            return
        owner = outbox.owner
        if owner is None:
            return
        if hasattr(owner, "email"):
            email = (getattr(owner, "email", "") or "").strip()
            if email:
                yield email
            return
        yield from self._iter_group_emails(owner)

    def _iter_notification_recipients(self) -> Iterator[str]:
        seen: set[str] = set()

        if self.assigned_user_id and self.assigned_user:
            email = (self.assigned_user.email or "").strip()
            if email and email.lower() not in seen:
                seen.add(email.lower())
                yield email

        for email in self._iter_group_emails(self.assigned_group):
            normalized = email.lower()
            if normalized not in seen:
                seen.add(normalized)
                yield email

        for email in self._iter_node_admin_emails():
            normalized = email.lower()
            if normalized not in seen:
                seen.add(normalized)
                yield email

    def resolve_notification_recipients(self) -> list[str]:
        return list(self._iter_notification_recipients())

    def _format_datetime(self, value) -> str:
        if not value:
            return ""
        try:
            localized = timezone.localtime(value)
        except Exception:
            localized = value
        return formats.date_format(localized, "DATETIME_FORMAT")

    def _notification_subject(self, trigger: str) -> str:
        if trigger == "immediate":
            template = _("Manual task assigned: %(title)s")
        elif trigger == "24h":
            template = _("Manual task starts in 24 hours: %(title)s")
        elif trigger == "3h":
            template = _("Manual task starts in 3 hours: %(title)s")
        else:
            template = _("Manual task reminder: %(title)s")
        title = self.category.name if self.category else self.description
        return template % {"title": title or _("Manual task")}

    def _notification_body(self) -> str:
        lines = [self.description or ""]
        if self.scheduled_start:
            lines.append(
                _("Starts: %(start)s")
                % {"start": self._format_datetime(self.scheduled_start)}
            )
        if self.scheduled_end:
            lines.append(
                _("Ends: %(end)s")
                % {"end": self._format_datetime(self.scheduled_end)}
            )
        if self.node_id:
            lines.append(_("Node: %(node)s") % {"node": self.node})
        if self.location_id:
            lines.append(_("Location: %(location)s") % {"location": self.location})
        return "\n".join(line for line in lines if line)

    def send_notification_email(self, trigger: str) -> bool:
        recipients = self.resolve_notification_recipients()
        if not recipients:
            return False
        subject = self._notification_subject(trigger)
        body = self._notification_body()
        if self.node_id and self.node:
            self.node.send_mail(subject, body, recipients)
        else:
            mailer.send(subject, body, recipients)
        return True

    def _schedule_notification_task(
        self, trigger: str, eta: timezone.datetime | None = None
    ) -> None:
        from apps.tasks.tasks import send_manual_task_notification

        kwargs = {"manual_task_id": self.pk, "trigger": trigger}
        if eta is None:
            send_manual_task_notification.apply_async(kwargs=kwargs)
        else:
            send_manual_task_notification.apply_async(kwargs=kwargs, eta=eta)

    def schedule_notifications(self) -> None:
        if not self.enable_notifications:
            return
        if not is_celery_enabled():
            return
        if not mailer.can_send_email():
            return
        self._schedule_notification_task("immediate")
        if not self.scheduled_start:
            return
        start = self.scheduled_start
        if timezone.is_naive(start):
            start = timezone.make_aware(start, timezone.get_current_timezone())
        now = timezone.now()
        reminders: Sequence[tuple[str, timezone.datetime]] = (
            ("24h", start - timedelta(hours=24)),
            ("3h", start - timedelta(hours=3)),
        )
        for trigger, eta in reminders:
            if eta <= now:
                continue
            self._schedule_notification_task(trigger, eta=eta)

    # Reservation helpers --------------------------------------------

    def _iter_reservation_users(self) -> Iterator[CoreUser]:
        if self.assigned_user_id and self.assigned_user:
            yield self.assigned_user
        if self.assigned_group_id and self.assigned_group:
            for user in self.assigned_group.user_set.filter(is_active=True):
                yield user
        node = self.node
        if not node:
            return
        outbox = getattr(node, "email_outbox", None)
        if not outbox:
            return
        owner = outbox.owner
        if owner is None:
            return
        if isinstance(owner, CoreUser):
            yield owner
        elif isinstance(owner, CoreSecurityGroup):
            for user in owner.user_set.filter(is_active=True):
                yield user

    def resolve_reservation_credentials(self):
        from apps.energy.models import CustomerAccount
        from apps.cards.models import RFID

        account: CustomerAccount | None = None
        rfid: RFID | None = None

        for candidate in self._iter_reservation_users():
            try:
                account = candidate.customer_account
            except CustomerAccount.DoesNotExist:
                account = None
            if not account:
                continue
            rfid = account.rfids.filter(allowed=True).order_by("pk").first()
            if rfid:
                break
        if not rfid or not account:
            return None, None, ""
        return account, rfid, rfid.rfid

    def create_cp_reservation(self):
        from apps.ocpp.models import CPReservation

        if not self.location_id or not self.location:
            raise ValidationError(
                {"location": _("Select a location before reserving a connector.")}
            )
        if not self.scheduled_start or not self.scheduled_end:
            raise ValidationError(
                {
                    "scheduled_start": _("Provide a full schedule before reserving."),
                    "scheduled_end": _("Provide a full schedule before reserving."),
                }
            )
        duration_seconds = (self.scheduled_end - self.scheduled_start).total_seconds()
        duration_minutes = max(1, int(ceil(duration_seconds / 60)))
        account, rfid, id_tag = self.resolve_reservation_credentials()
        if not id_tag:
            raise ValidationError(
                _("Unable to determine an RFID tag for the assigned contacts.")
            )

        reservation = CPReservation(
            location=self.location,
            start_time=self.scheduled_start,
            duration_minutes=duration_minutes,
            account=account,
            rfid=rfid,
            id_tag=id_tag,
        )
        reservation.full_clean(exclude=["connector"])
        reservation.save()
        reservation.send_reservation_request()
        return reservation

    def save(self, *args, **kwargs):
        track_fields = (
            "enable_notifications",
            "scheduled_start",
            "scheduled_end",
            "assigned_user_id",
            "assigned_group_id",
        )
        previous = None
        if self.pk:
            previous = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values(*track_fields)
                .first()
            )
        super().save(*args, **kwargs)
        should_schedule = False
        if self.enable_notifications:
            if not previous:
                should_schedule = True
            else:
                for field in track_fields:
                    old_value = previous.get(field)
                    new_value = getattr(self, field)
                    if old_value != new_value:
                        should_schedule = True
                        break
        if should_schedule:
            self.schedule_notifications()
