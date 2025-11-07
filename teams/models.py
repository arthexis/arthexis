import json
import logging
from datetime import timedelta
from math import ceil
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q
from django.utils import formats, timezone
from django.utils.translation import gettext_lazy as _

from core.entity import Entity
from core.fields import SigilShortAutoField
from core import mailer
from core.models import (
    InviteLead as CoreInviteLead,
    User as CoreUser,
    SecurityGroup as CoreSecurityGroup,
    EmailInbox as CoreEmailInbox,
    EmailCollector as CoreEmailCollector,
    ReleaseManager as CoreReleaseManager,
    OdooProfile as CoreOdooProfile,
    GoogleCalendarProfile as CoreGoogleCalendarProfile,
    Profile as CoreProfile,
)
from awg.models import PowerLead as CorePowerLead
from django_otp.plugins.otp_totp.models import (
    TOTPDevice as CoreTOTPDevice,
)
from nodes.models import EmailOutbox as CoreEmailOutbox


logger = logging.getLogger(__name__)


class SlackApiError(RuntimeError):
    """Raised when Slack reports an error during API calls."""


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


class SlackBotProfile(CoreProfile):
    """Store credentials required to operate a Slack chatbot."""

    API_BASE_URL = "https://slack.com/api"

    profile_fields = (
        "node",
        "team_id",
        "bot_user_id",
        "bot_token",
        "signing_secret",
        "default_channels",
        "is_enabled",
    )

    node = models.OneToOneField(
        "nodes.Node",
        on_delete=models.CASCADE,
        related_name="slack_bot",
        null=True,
        blank=True,
        help_text=_("Node that owns this Slack chatbot."),
    )
    team_id = models.CharField(
        max_length=32,
        help_text=_("Slack workspace team identifier (starts with T)."),
    )
    bot_user_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Slack bot user identifier (starts with U or B)."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack bot token used for authenticated API calls."),
    )
    signing_secret = SigilShortAutoField(
        max_length=255,
        help_text=_("Slack signing secret used to verify incoming requests."),
    )
    default_channels = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Channel identifiers where Net Messages should be posted. Provide"
            " a JSON array of channel IDs (for example [\"C01ABCDE\"])."
        ),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop the bot from posting to Slack."),
    )

    class Meta:
        verbose_name = _("Slack Chatbot")
        verbose_name_plural = _("Slack Chatbots")
        constraints = [
            models.UniqueConstraint(
                fields=["team_id"],
                name="slackbotprofile_team_id_unique",
            ),
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                    | (Q(user__isnull=True) & Q(group__isnull=True) & Q(node__isnull=False))
                ),
                name="slackbotprofile_requires_owner",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        identifier = (self.resolve_sigils("team_id") or self.team_id or "").strip()
        owner = self.owner_display()
        if identifier and owner:
            return f"{identifier} ({owner})"
        if identifier:
            return identifier
        return owner or super().__str__()

    def clean(self):
        if self.user_id or self.group_id:
            super().clean()
        else:
            super(CoreProfile, self).clean()

        errors = {}

        if not self.node_id and not self.user_id and not self.group_id:
            errors["node"] = _("Assign the Slack bot to a node or owner.")

        team_id = (self.team_id or "").strip().upper()
        if not team_id:
            errors["team_id"] = _("Provide the Slack workspace team identifier.")
        elif not team_id.startswith("T"):
            errors["team_id"] = _("Slack team identifiers start with the letter T.")

        if not (self.resolve_sigils("bot_token") or (self.bot_token or "").strip()):
            errors["bot_token"] = _(
                "Provide the Slack bot token so Arthexis can send messages."
            )

        if not (self.resolve_sigils("signing_secret") or (self.signing_secret or "").strip()):
            errors["signing_secret"] = _(
                "Provide the Slack signing secret so incoming requests can be verified."
            )

        channels_error = self._normalize_channels()
        if channels_error:
            errors["default_channels"] = channels_error

        if errors:
            raise ValidationError(errors)

        self.team_id = team_id

    def save(self, *args, **kwargs):
        if self.team_id:
            self.team_id = self.team_id.strip().upper()
        if self.bot_user_id:
            self.bot_user_id = self.bot_user_id.strip().upper()
        self._normalize_channels()
        super().save(*args, **kwargs)

    def owner_display(self):  # pragma: no cover - simple representation helper
        owner = super().owner_display()
        if owner:
            return owner
        if self.node_id and self.node:
            return str(self.node)
        return ""

    def _normalize_channels(self) -> str | None:
        """Ensure ``default_channels`` stores a list of strings."""

        channels = self.default_channels or []
        if isinstance(channels, str):
            try:
                channels = json.loads(channels)
            except json.JSONDecodeError:
                return _("Channel IDs must be provided as a JSON array of strings.")
        if channels is None:
            channels = []
        if not isinstance(channels, list):
            return _("Channel IDs must be provided as a list of strings.")
        normalized: list[str] = []
        for value in channels:
            if value is None:
                continue
            if not isinstance(value, str):
                return _("Channel IDs must be provided as a list of strings.")
            cleaned = value.strip()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        self.default_channels = normalized
        return None

    # Public helpers -------------------------------------------------

    def get_bot_token(self) -> str:
        return (self.resolve_sigils("bot_token") or self.bot_token or "").strip()

    def get_signing_secret(self) -> str:
        return (self.resolve_sigils("signing_secret") or self.signing_secret or "").strip()

    def get_channels(self) -> list[str]:
        return list(self.default_channels or [])

    def connect(self) -> dict[str, object]:
        """Validate the stored credentials by calling Slack's ``auth.test``."""

        data = self._api_post("auth.test", {})
        team_id = (data.get("team_id") or "").strip().upper()
        bot_user_id = (data.get("user_id") or "").strip().upper()
        updated_fields: list[str] = []
        if team_id and not self.team_id:
            self.team_id = team_id
            updated_fields.append("team_id")
        if bot_user_id and not self.bot_user_id:
            self.bot_user_id = bot_user_id
            updated_fields.append("bot_user_id")
        if updated_fields:
            self.save(update_fields=updated_fields)
        return data

    def broadcast_net_message(self, message: "NetMessage") -> None:
        """Post ``message`` to each configured Slack channel."""

        if not self.is_enabled:
            return
        token = self.get_bot_token()
        if not token:
            logger.debug("Slack bot %s skipped broadcast: missing token", self.pk)
            return
        channels = self.get_channels()
        if not channels:
            logger.debug(
                "Slack bot %s skipped broadcast: no default channels configured",
                self.pk,
            )
            return

        subject = (message.subject or "").strip()
        body = (message.body or "").strip()
        if subject and body:
            text = f"*{subject}*\n{body}"
        else:
            text = subject or body

        attachments = []
        for attachment in message.attachments or []:
            if not isinstance(attachment, dict):
                continue
            descriptor = attachment.get("description")
            if isinstance(descriptor, str) and descriptor.strip():
                attachments.append(descriptor.strip())
                continue
            try:
                attachments.append(json.dumps(attachment, ensure_ascii=False))
            except (TypeError, ValueError):
                continue

        for channel in channels:
            payload: dict[str, object] = {"channel": channel, "text": text or ""}
            if attachments:
                attachment_text = "\n".join(attachments)
                payload["text"] = (payload["text"] or "").strip()
                if payload["text"]:
                    payload["text"] += "\n" + attachment_text
                else:
                    payload["text"] = attachment_text
            try:
                self._api_post("chat.postMessage", payload)
            except SlackApiError:
                logger.exception(
                    "Slack bot %s failed to post NetMessage %s to channel %s",
                    self.pk,
                    getattr(message, "pk", None),
                    channel,
                )

    # Internal utilities --------------------------------------------

    def _api_post(self, method: str, payload: dict[str, object]) -> dict[str, object]:
        token = self.get_bot_token()
        if not token:
            raise SlackApiError("missing_token")
        url = f"{self.API_BASE_URL}/{method}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
        except requests.RequestException as exc:  # pragma: no cover - network issues
            raise SlackApiError("request_failed") from exc
        data: dict[str, object] | None
        try:
            data = response.json()
        except ValueError:
            data = None
        if not response.ok or not isinstance(data, dict) or not data.get("ok"):
            error = "unknown_error"
            if isinstance(data, dict):
                error = str(data.get("error") or error)
            raise SlackApiError(error)
        return data

    def _profile_fields(self) -> Iterable[str]:  # pragma: no cover - admin helper
        return self.profile_fields


class ManualTask(Entity):
    """Manual work scheduled for nodes or charge locations."""

    title = models.CharField(_("Title"), max_length=200)
    description = models.TextField(
        _("Description"), help_text=_("Detailed summary of the work to perform."),
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
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_manual_tasks",
        verbose_name=_("Assigned group"),
        help_text=_("Optional security group responsible for the task."),
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

    # Notification helpers -------------------------------------------

    @staticmethod
    def _celery_lock_path() -> Path:
        return Path(settings.BASE_DIR) / "locks" / "celery.lck"

    @classmethod
    def _is_celery_enabled(cls) -> bool:
        return cls._celery_lock_path().exists()

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
        return template % {"title": self.title}

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
            lines.append(_("Charge location: %(location)s") % {"location": self.location})
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
        from teams.tasks import send_manual_task_notification

        kwargs = {"manual_task_id": self.pk, "trigger": trigger}
        if eta is None:
            send_manual_task_notification.apply_async(kwargs=kwargs)
        else:
            send_manual_task_notification.apply_async(kwargs=kwargs, eta=eta)

    def schedule_notifications(self) -> None:
        if not self.enable_notifications:
            return
        if not self._is_celery_enabled():
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
        from core.models import EnergyAccount, RFID

        account: EnergyAccount | None = None
        rfid: RFID | None = None

        for candidate in self._iter_reservation_users():
            try:
                account = candidate.energy_account
            except EnergyAccount.DoesNotExist:
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
        from ocpp.models import CPReservation

        if not self.location_id or not self.location:
            raise ValidationError(
                {"location": _("Select a charge location before reserving a connector.")}
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


