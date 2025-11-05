from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from core.entity import Entity
from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
    GoogleCalendarProfile as CoreGoogleCalendarProfile,
)
from awg.models import PowerLead as CorePowerLead
from django_otp.plugins.otp_totp.models import (
    TOTPDevice as CoreTOTPDevice,
)
from nodes.models import EmailOutbox as CoreEmailOutbox


class InviteLead(CoreInviteLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreInviteLead._meta.verbose_name
        verbose_name_plural = CoreInviteLead._meta.verbose_name_plural


class PowerLead(CorePowerLead):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CorePowerLead._meta.verbose_name
        verbose_name_plural = CorePowerLead._meta.verbose_name_plural


class User(CoreUser):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreUser._meta.verbose_name
        verbose_name_plural = CoreUser._meta.verbose_name_plural


class SecurityGroup(CoreSecurityGroup):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreSecurityGroup._meta.verbose_name
        verbose_name_plural = CoreSecurityGroup._meta.verbose_name_plural


class EmailInbox(CoreEmailInbox):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailInbox._meta.verbose_name
        verbose_name_plural = CoreEmailInbox._meta.verbose_name_plural


class EmailCollector(CoreEmailCollector):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailCollector._meta.verbose_name
        verbose_name_plural = CoreEmailCollector._meta.verbose_name_plural


class ReleaseManager(CoreReleaseManager):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreReleaseManager._meta.verbose_name
        verbose_name_plural = CoreReleaseManager._meta.verbose_name_plural


class EmailOutbox(CoreEmailOutbox):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreEmailOutbox._meta.verbose_name
        verbose_name_plural = CoreEmailOutbox._meta.verbose_name_plural


class OdooProfile(CoreOdooProfile):
    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreOdooProfile._meta.verbose_name
        verbose_name_plural = CoreOdooProfile._meta.verbose_name_plural


class TOTPDevice(CoreTOTPDevice):
    supports_user_datum = True
    supports_seed_datum = True

    class Meta:
        proxy = True
        app_label = "teams"
        verbose_name = CoreTOTPDevice._meta.verbose_name
        verbose_name_plural = CoreTOTPDevice._meta.verbose_name_plural


class GoogleCalendarProfile(CoreGoogleCalendarProfile):
    class Meta:
        proxy = True
        app_label = "django_celery_beat"
        verbose_name = CoreGoogleCalendarProfile._meta.verbose_name
        verbose_name_plural = CoreGoogleCalendarProfile._meta.verbose_name_plural


class ManualTask(Entity):
    """Manual work scheduled for nodes or charge locations."""

    title = models.CharField(_("Title"), max_length=200)
    description = models.TextField(
        _("Description"), help_text=_("Detailed summary of the work to perform."),
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
        "ocpp.Location",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="manual_tasks",
        verbose_name=_("Charge Location"),
        help_text=_("Charge point location associated with this manual task."),
    )
    scheduled_start = models.DateTimeField(
        _("Scheduled start"), help_text=_("Planned start time for this work."),
    )
    scheduled_end = models.DateTimeField(
        _("Scheduled end"), help_text=_("Planned completion time for this work."),
    )

    class Meta:
        verbose_name = _("Manual Task")
        verbose_name_plural = _("Manual Tasks")
        ordering = ("scheduled_start", "title")
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
            message = _("Select at least one node or charge location.")
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
        return self.title


