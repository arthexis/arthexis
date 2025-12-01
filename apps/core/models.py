from django.contrib.auth.models import (
    AbstractUser,
    Group,
    UserManager as DjangoUserManager,
)
from django.db import DatabaseError, IntegrityError, connections, models, transaction
from django.db.models import Q, F
from django.db.models.functions import Lower, Length
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _, gettext, override
from django.utils.html import format_html
from django.core.validators import (
    MaxValueValidator,
    MinValueValidator,
    RegexValidator,
    validate_ipv46_address,
)
from django.core.exceptions import ValidationError
from django.apps import apps
from django.db.models.signals import m2m_changed
from django.dispatch import receiver
from django.views.decorators.debug import sensitive_variables
from datetime import (
    time as datetime_time,
    timedelta,
    datetime as datetime_datetime,
    date as datetime_date,
    timezone as datetime_timezone,
)
import contextlib
import logging
import json
import base64
from decimal import Decimal
import hashlib
import os
import subprocess
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode
from django.utils import timezone, formats
from django.utils.dateparse import parse_datetime
from packaging.version import InvalidVersion, Version
import uuid
from pathlib import Path
from django.core import serializers
from django.core.management.color import no_style
from urllib.parse import quote, quote_plus, urlparse
from zoneinfo import ZoneInfo
from utils import revision as revision_utils
from apps.celery.utils import normalize_periodic_task_name
from apps.core.language import default_report_language
from typing import Any, Type
import requests

logger = logging.getLogger(__name__)


from apps.base.models import Entity, EntityManager, EntityUserManager
from . import temp_passwords
from apps.sigils.fields import (
    SigilShortAutoField,
    ConditionTextField,
    ConditionCheckResult,
)


class SecurityGroup(Group):
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )

    class Meta:
        verbose_name = "Security Group"
        verbose_name_plural = "Security Groups"


class Profile(Entity):
    """Abstract base class for user or group scoped configuration."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    group = models.OneToOneField(
        "core.SecurityGroup",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        if self.user_id and self.group_id:
            raise ValidationError(
                {
                    "user": _("Select either a user or a security group, not both."),
                    "group": _("Select either a user or a security group, not both."),
                }
            )
        if not self.user_id and not self.group_id:
            raise ValidationError(
                _("Profiles must be assigned to a user or a security group."),
            )
        if self.user_id:
            user_model = get_user_model()
            username_cache = {"value": None}

            def _resolve_username():
                if username_cache["value"] is not None:
                    return username_cache["value"]
                user_obj = getattr(self, "user", None)
                username = getattr(user_obj, "username", None)
                if not username:
                    manager = getattr(
                        user_model, "all_objects", user_model._default_manager
                    )
                    username = (
                        manager.filter(pk=self.user_id)
                        .values_list("username", flat=True)
                        .first()
                    )
                username_cache["value"] = username
                return username

            is_restricted = getattr(user_model, "is_profile_restricted_username", None)
            if callable(is_restricted):
                username = _resolve_username()
                if is_restricted(username):
                    raise ValidationError(
                        {
                            "user": _(
                                "The %(username)s account cannot have profiles attached."
                            )
                            % {"username": username}
                        }
                    )
            else:
                system_username = getattr(user_model, "SYSTEM_USERNAME", None)
                if system_username:
                    username = _resolve_username()
                    if user_model.is_system_username(username):
                        raise ValidationError(
                            {
                                "user": _(
                                    "The %(username)s account cannot have profiles attached."
                                )
                                % {"username": username}
                            }
                        )

    @property
    def owner(self):
        """Return the assigned user or group."""

        return self.user if self.user_id else self.group

    def owner_display(self) -> str:
        """Return a human readable owner label."""

        owner = self.owner
        if owner is None:  # pragma: no cover - guarded by ``clean``
            return ""
        if hasattr(owner, "get_username"):
            return owner.get_username()
        if hasattr(owner, "name"):
            return owner.name
        return str(owner)


class Lead(Entity):
    """Common request lead information."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ASSIGNED = "assigned", _("Assigned")
        CLOSED = "closed", _("Closed")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    path = models.TextField(blank=True)
    referer = models.TextField(blank=True)
    user_agent = models.TextField(blank=True)
    ip_address = models.CharField(
        max_length=45,
        blank=True,
        validators=[validate_ipv46_address],
    )
    created_on = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.OPEN
    )
    assign_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="%(app_label)s_%(class)s_assignments",
    )

    class Meta:
        abstract = True


class InviteLead(Lead):
    email = models.EmailField()
    comment = models.TextField(blank=True)
    sent_on = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    mac_address = models.CharField(max_length=17, blank=True)
    sent_via_outbox = models.ForeignKey(
        "emails.EmailOutbox",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="invite_leads",
    )

    class Meta:
        verbose_name = "Invite Lead"
        verbose_name_plural = "Invite Leads"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.email


class User(Entity, AbstractUser):
    SYSTEM_USERNAME = "arthexis"
    ADMIN_USERNAME = "admin"
    PROFILE_RESTRICTED_USERNAMES = frozenset()

    objects = EntityUserManager()
    all_objects = DjangoUserManager()
    """Custom user model."""
    data_path = models.CharField(max_length=255, blank=True)
    last_visit_ip_address = models.CharField(
        max_length=45,
        blank=True,
        validators=[validate_ipv46_address],
    )
    operate_as = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="operated_users",
        help_text=(
            "Operate using another user's permissions when additional authority is "
            "required."
        ),
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
        help_text=(
            "Designates whether this user should be treated as active. Unselect this instead of deleting customer accounts."
        ),
    )
    require_2fa = models.BooleanField(
        _("require 2FA"),
        default=False,
        help_text=_("Require both a password and authenticator code to sign in."),
    )
    temporary_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Automatically deactivate this account after the selected date and time."),
    )

    def __str__(self):
        return self.username

    @classmethod
    def is_system_username(cls, username):
        return bool(username) and username == cls.SYSTEM_USERNAME

    @sensitive_variables("raw_password")
    def set_password(self, raw_password):
        result = super().set_password(raw_password)
        temp_passwords.discard_temp_password(self.username)
        return result

    @sensitive_variables("raw_password")
    def check_password(self, raw_password):
        if self._deactivate_if_expired():
            return False
        if super().check_password(raw_password):
            return True
        if raw_password is None:
            return False
        entry = temp_passwords.load_temp_password(self.username)
        if entry is None:
            return False
        if entry.is_expired:
            temp_passwords.discard_temp_password(self.username)
            return False
        if not entry.allow_change:
            return False
        return entry.check_password(raw_password)

    def _normalized_expiration(self):
        expires_at = self.temporary_expires_at
        if expires_at and timezone.is_naive(expires_at):
            expires_at = timezone.make_aware(expires_at)
        return expires_at

    @property
    def is_temporary(self) -> bool:
        return self.temporary_expires_at is not None

    @property
    def is_temporarily_expired(self) -> bool:
        expires_at = self._normalized_expiration()
        return bool(expires_at and timezone.now() >= expires_at)

    def _deactivate_if_expired(self, *, save: bool = True) -> bool:
        if not self.is_temporarily_expired:
            return False
        updates = []
        normalized_expiration = self._normalized_expiration()
        if normalized_expiration and self.temporary_expires_at != normalized_expiration:
            self.temporary_expires_at = normalized_expiration
            updates.append("temporary_expires_at")
        if self.is_active:
            self.is_active = False
            updates.append("is_active")
        temp_passwords.discard_temp_password(self.username)
        if updates and save and self.pk:
            type(self).all_objects.filter(pk=self.pk).update(
                **{field: getattr(self, field) for field in updates}
            )
        return True

    def deactivate_temporary_credentials(self):
        if self.temporary_expires_at is None or self.temporary_expires_at > timezone.now():
            self.temporary_expires_at = timezone.now()
        self.is_active = False
        temp_passwords.discard_temp_password(self.username)
        updates = ["temporary_expires_at", "is_active"]
        if self.pk:
            type(self).all_objects.filter(pk=self.pk).update(
                temporary_expires_at=self.temporary_expires_at, is_active=self.is_active
            )

    @classmethod
    def is_profile_restricted_username(cls, username):
        return bool(username) and username in cls.PROFILE_RESTRICTED_USERNAMES

    @property
    def is_system_user(self) -> bool:
        return self.is_system_username(self.username)

    @property
    def is_profile_restricted(self) -> bool:
        return self.is_profile_restricted_username(self.username)

    def clean(self):
        super().clean()
        if not self.operate_as_id:
            return
        try:
            delegate = self.operate_as
        except type(self).DoesNotExist:
            raise ValidationError({"operate_as": _("Selected user is not available.")})
        errors = []
        if delegate.pk == self.pk:
            errors.append(_("Cannot operate as yourself."))
        if getattr(delegate, "is_deleted", False):
            errors.append(_("Cannot operate as a deleted user."))
        if not self.is_staff:
            errors.append(_("Only staff members may operate as another user."))
        if delegate.is_staff and not self.is_superuser:
            errors.append(_("Only superusers may operate as staff members."))
        if errors:
            raise ValidationError({"operate_as": errors})

    def _delegate_for_permissions(self):
        if not self.is_staff or not self.operate_as_id:
            return None
        try:
            delegate = self.operate_as
        except type(self).DoesNotExist:
            return None
        if delegate.pk == self.pk:
            return None
        if getattr(delegate, "is_deleted", False):
            return None
        if delegate.is_staff and not self.is_superuser:
            return None
        return delegate

    def _check_operate_as_chain(self, predicate, visited=None):
        if visited is None:
            visited = set()
        identifier = self.pk or id(self)
        if identifier in visited:
            return False
        visited.add(identifier)
        if predicate(self):
            return True
        delegate = self._delegate_for_permissions()
        if not delegate:
            return False
        return delegate._check_operate_as_chain(predicate, visited)

    def has_perm(self, perm, obj=None):
        return self._check_operate_as_chain(
            lambda user: super(User, user).has_perm(perm, obj)
        )

    def has_module_perms(self, app_label):
        return self._check_operate_as_chain(
            lambda user: super(User, user).has_module_perms(app_label)
        )

    def _profile_for(self, profile_cls: Type[Profile], user: "User"):
        queryset = profile_cls.objects.all()
        if hasattr(profile_cls, "is_enabled"):
            queryset = queryset.filter(is_enabled=True)

        profile = queryset.filter(user=user).first()
        if profile:
            return profile
        group_ids = list(user.groups.values_list("id", flat=True))
        if group_ids:
            return queryset.filter(group_id__in=group_ids).first()
        return None

    def get_profile(self, profile_cls: Type[Profile]):
        """Return the first matching profile for the user or their delegate chain."""

        if not isinstance(profile_cls, type) or not issubclass(profile_cls, Profile):
            raise TypeError("profile_cls must be a Profile subclass")

        result = None

        def predicate(user: "User"):
            nonlocal result
            result = self._profile_for(profile_cls, user)
            return result is not None

        self._check_operate_as_chain(predicate)
        return result

    def has_profile(self, profile_cls: Type[Profile]) -> bool:
        """Return ``True`` when a profile is available for the user or delegate chain."""

        return self.get_profile(profile_cls) is not None

    def _direct_profile(self, model_label: str, app_label: str = "core"):
        model = apps.get_model(app_label, model_label)
        try:
            return self.get_profile(model)
        except TypeError:
            return None

    def get_phones_by_priority(self):
        """Return a list of ``UserPhoneNumber`` instances ordered by priority."""

        ordered_numbers = self.phone_numbers.order_by("priority", "pk")
        return list(ordered_numbers)

    def get_phone_numbers_by_priority(self):
        """Backward-compatible alias for :meth:`get_phones_by_priority`."""

        return self.get_phones_by_priority()

    @property
    def release_manager(self):
        return self._direct_profile("ReleaseManager")

    @property
    def odoo_employee(self):
        return self._direct_profile("OdooEmployee", app_label="odoo")

    @property
    def odoo_profile(self):
        return self.odoo_employee

    @property
    def social_profile(self):
        model = apps.get_model("teams", "SocialProfile")
        try:
            return self.get_profile(model)
        except TypeError:
            return None

    @property
    def google_calendar_profile(self):
        return self._direct_profile("GoogleCalendarProfile")


    class Meta(AbstractUser.Meta):
        verbose_name = _("User")
        verbose_name_plural = _("Users")


class UserPhoneNumber(Entity):
    """Store phone numbers associated with a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="phone_numbers",
    )
    number = models.CharField(
        max_length=20,
        help_text="Contact phone number",
    )
    priority = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ("priority", "id")
        verbose_name = "Phone Number"
        verbose_name_plural = "Phone Numbers"

    def __str__(self):  # pragma: no cover - simple representation
        return f"{self.number} ({self.priority})"





class GoogleCalendarProfile(Profile):
    """Store Google Calendar configuration for a user or security group."""

    profile_fields = ("calendar_id", "api_key", "display_name", "timezone")

    calendar_id = SigilShortAutoField(
        max_length=255, verbose_name=_("Calendar ID")
    )
    api_key = SigilShortAutoField(max_length=255, verbose_name=_("API Key"))
    display_name = models.CharField(
        max_length=255, blank=True, verbose_name=_("Display Name")
    )
    max_events = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text=_("Number of upcoming events to display (1-20)."),
    )
    timezone = SigilShortAutoField(
        max_length=100, blank=True, verbose_name=_("Time Zone")
    )

    GOOGLE_EVENTS_URL = (
        "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
    )
    GOOGLE_EMBED_URL = "https://calendar.google.com/calendar/embed?src={calendar}&ctz={tz}"

    class Meta:
        verbose_name = _("Google Calendar")
        verbose_name_plural = _("Google Calendars")
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="googlecalendarprofile_requires_owner",
            )
        ]

    def __str__(self):  # pragma: no cover - simple representation
        label = self.get_display_name()
        return label or self.resolved_calendar_id()

    def resolved_calendar_id(self) -> str:
        value = self.resolve_sigils("calendar_id")
        return value or self.calendar_id or ""

    def resolved_api_key(self) -> str:
        value = self.resolve_sigils("api_key")
        return value or self.api_key or ""

    def resolved_timezone(self) -> str:
        value = self.resolve_sigils("timezone")
        return value or self.timezone or ""

    def get_timezone(self) -> ZoneInfo:
        tz_name = self.resolved_timezone() or settings.TIME_ZONE
        try:
            return ZoneInfo(tz_name)
        except Exception:
            return ZoneInfo("UTC")

    def get_display_name(self) -> str:
        value = self.resolve_sigils("display_name")
        if value:
            return value
        if self.display_name:
            return self.display_name
        return ""

    def build_events_url(self) -> str:
        calendar = self.resolved_calendar_id().strip()
        if not calendar:
            return ""
        encoded = quote(calendar, safe="@")
        return self.GOOGLE_EVENTS_URL.format(calendar=encoded)

    def build_calendar_url(self) -> str:
        calendar = self.resolved_calendar_id().strip()
        if not calendar:
            return ""
        tz = self.get_timezone().key
        encoded_calendar = quote_plus(calendar)
        encoded_tz = quote_plus(tz)
        return self.GOOGLE_EMBED_URL.format(calendar=encoded_calendar, tz=encoded_tz)

    def _parse_event_point(self, data: dict) -> tuple[datetime_datetime | None, bool]:
        if not isinstance(data, dict):
            return None, False

        tz_name = data.get("timeZone")
        default_tz = self.get_timezone()
        tzinfo = default_tz
        if tz_name:
            try:
                tzinfo = ZoneInfo(tz_name)
            except Exception:
                tzinfo = default_tz

        timestamp = data.get("dateTime")
        if timestamp:
            dt = parse_datetime(timestamp)
            if dt is None:
                try:
                    dt = datetime_datetime.fromisoformat(
                        timestamp.replace("Z", "+00:00")
                    )
                except ValueError:
                    dt = None
            if dt is not None and dt.tzinfo is None:
                dt = dt.replace(tzinfo=tzinfo)
            return dt, False

        date_value = data.get("date")
        if date_value:
            try:
                day = datetime_date.fromisoformat(date_value)
            except ValueError:
                return None, True
            dt = datetime_datetime.combine(day, datetime_time.min, tzinfo=tzinfo)
            return dt, True

        return None, False

    def fetch_events(self, *, max_results: int | None = None) -> list[dict[str, object]]:
        calendar_id = self.resolved_calendar_id().strip()
        api_key = self.resolved_api_key().strip()
        if not calendar_id or not api_key:
            return []

        url = self.build_events_url()
        if not url:
            return []

        now = timezone.now().astimezone(datetime_timezone.utc).replace(microsecond=0)
        params = {
            "key": api_key,
            "singleEvents": "true",
            "orderBy": "startTime",
            "timeMin": now.isoformat().replace("+00:00", "Z"),
            "maxResults": max_results or self.max_events or 5,
        }

        response = None
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError):
            logger.warning(
                "Failed to fetch Google Calendar events for profile %s", self.pk,
                exc_info=True,
            )
            return []
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()

        items = payload.get("items")
        if not isinstance(items, list):
            return []

        events: list[dict[str, object]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            start, all_day = self._parse_event_point(item.get("start") or {})
            end, _ = self._parse_event_point(item.get("end") or {})
            summary = item.get("summary") or ""
            link = item.get("htmlLink") or ""
            location = item.get("location") or ""
            if start is None:
                continue
            events.append(
                {
                    "summary": summary,
                    "start": start,
                    "end": end,
                    "all_day": all_day,
                    "html_link": link,
                    "location": location,
                }
            )

        events.sort(key=lambda event: event.get("start") or timezone.now())
        return events
class EmailArtifact(Entity):
    """Store messages discovered by :class:`EmailCollector`."""

    collector = models.ForeignKey(
        "emails.EmailCollector", related_name="artifacts", on_delete=models.CASCADE
    )
    subject = models.CharField(max_length=255)
    sender = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    sigils = models.JSONField(default=dict)
    fingerprint = models.CharField(max_length=32)

    @staticmethod
    def fingerprint_for(subject: str, sender: str, body: str) -> str:
        import hashlib

        data = (subject or "") + (sender or "") + (body or "")
        hasher = hashlib.md5(data.encode("utf-8"), usedforsecurity=False)
        return hasher.hexdigest()

    class Meta:
        unique_together = ("collector", "fingerprint")
        verbose_name = "Email Artifact"
        verbose_name_plural = "Email Artifacts"
        ordering = ["-id"]


class EmailTransaction(Entity):
    """Persist inbound and outbound email messages and their metadata."""

    INBOUND = "inbound"
    OUTBOUND = "outbound"
    DIRECTION_CHOICES = [
        (INBOUND, "Inbound"),
        (OUTBOUND, "Outbound"),
    ]

    STATUS_COLLECTED = "collected"
    STATUS_QUEUED = "queued"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_COLLECTED, "Collected"),
        (STATUS_QUEUED, "Queued"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    direction = models.CharField(
        max_length=8,
        choices=DIRECTION_CHOICES,
        default=INBOUND,
        help_text="Whether the message originated from an inbox or is being sent out.",
    )
    status = models.CharField(
        max_length=9,
        choices=STATUS_CHOICES,
        default=STATUS_COLLECTED,
        help_text="Lifecycle stage for the stored email message.",
    )
    collector = models.ForeignKey(
        "emails.EmailCollector",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Collector that discovered this message, if applicable.",
    )
    inbox = models.ForeignKey(
        "emails.EmailInbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Inbox account the message was read from or will use for sending.",
    )
    outbox = models.ForeignKey(
        "emails.EmailOutbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Outbox configuration used to send the message, when known.",
    )
    message_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Message-ID header for threading and deduplication.",
    )
    thread_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Thread or conversation identifier, if provided by the provider.",
    )
    subject = models.CharField(max_length=998, blank=True)
    from_address = models.CharField(
        max_length=512,
        blank=True,
        help_text="From header as provided by the email message.",
    )
    sender_address = models.CharField(
        max_length=512,
        blank=True,
        help_text="Envelope sender address, if available.",
    )
    to_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of To recipient addresses.",
    )
    cc_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Cc recipient addresses.",
    )
    bcc_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Bcc recipient addresses.",
    )
    reply_to_addresses = models.JSONField(
        default=list,
        blank=True,
        help_text="List of Reply-To addresses from the message headers.",
    )
    headers = models.JSONField(
        default=dict,
        blank=True,
        help_text="Complete header map as parsed from the message.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Additional provider-specific metadata.",
    )
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    raw_content = models.TextField(
        blank=True,
        help_text="Raw RFC822 payload for the message, if stored.",
    )
    message_ts = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp supplied by the email headers.",
    )
    queued_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the message was queued for outbound delivery.",
    )
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the message was sent or fully processed.",
    )
    error = models.TextField(
        blank=True,
        help_text="Failure details captured during processing, if any.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if not (self.collector_id or self.inbox_id or self.outbox_id):
            raise ValidationError(
                {"direction": _("Select an inbox, collector or outbox for the transaction.")}
            )
        if self.direction == self.INBOUND and not (self.collector_id or self.inbox_id):
            raise ValidationError(
                {"inbox": _("Inbound messages must reference a collector or inbox.")}
            )
        if self.direction == self.OUTBOUND and not (self.outbox_id or self.inbox_id):
            raise ValidationError(
                {"outbox": _("Outbound messages must reference an inbox or outbox.")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        if self.subject:
            return self.subject
        if self.from_address:
            return self.from_address
        return super().__str__()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "Email Transaction"
        verbose_name_plural = "Email Transactions"
        indexes = [
            models.Index(fields=["message_id"], name="email_txn_msgid"),
            models.Index(fields=["direction", "status"], name="email_txn_dir_status"),
        ]


class EmailTransactionAttachment(Entity):
    """Attachment stored alongside an :class:`EmailTransaction`."""

    transaction = models.ForeignKey(
        EmailTransaction,
        related_name="attachments",
        on_delete=models.CASCADE,
    )
    filename = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=255, blank=True)
    content_id = models.CharField(
        max_length=255,
        blank=True,
        help_text="Identifier used for inline attachments.",
    )
    inline = models.BooleanField(
        default=False,
        help_text="Marks whether the attachment is referenced inline in the body.",
    )
    size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Size of the decoded attachment payload in bytes.",
    )
    content = models.TextField(
        blank=True,
        help_text="Base64 encoded attachment payload.",
    )

    def __str__(self):  # pragma: no cover - simple representation
        if self.filename:
            return self.filename
        return super().__str__()

    class Meta:
        verbose_name = "Email Attachment"
        verbose_name_plural = "Email Attachments"
class RFID(Entity):
    """RFID tag that may be assigned to one account."""

    label_id = models.AutoField(primary_key=True, db_column="label_id")
    MATCH_PREFIX_LENGTH = 8
    rfid = models.CharField(
        max_length=255,
        unique=True,
        verbose_name="RFID",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]+$",
                message="RFID must be hexadecimal digits",
            )
        ],
    )
    reversed_uid = models.CharField(
        max_length=255,
        default="",
        blank=True,
        editable=False,
        verbose_name="Reversed UID",
        help_text="UID value stored with opposite endianness for reference.",
    )
    custom_label = models.CharField(
        max_length=32,
        blank=True,
        verbose_name="Custom Label",
        help_text="Optional custom label for this RFID.",
    )
    key_a = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key A",
    )
    key_b = models.CharField(
        max_length=12,
        default="FFFFFFFFFFFF",
        validators=[
            RegexValidator(
                r"^[0-9A-Fa-f]{12}$",
                message="Key must be 12 hexadecimal digits",
            )
        ],
        verbose_name="Key B",
    )
    data = models.JSONField(
        default=list,
        blank=True,
        help_text="Sector and block data",
    )
    key_a_verified = models.BooleanField(default=False)
    key_b_verified = models.BooleanField(default=False)
    allowed = models.BooleanField(default=True)
    external_command = models.TextField(
        default="",
        blank=True,
        help_text="Optional command executed during validation.",
    )
    post_auth_command = models.TextField(
        default="",
        blank=True,
        help_text="Optional command executed after successful validation.",
    )
    expiry_date = models.DateField(
        null=True,
        blank=True,
        help_text="Optional expiration date for this RFID card.",
    )
    BLACK = "B"
    WHITE = "W"
    BLUE = "U"
    RED = "R"
    GREEN = "G"
    COLOR_CHOICES = [
        (BLACK, _("Black")),
        (WHITE, _("White")),
        (BLUE, _("Blue")),
        (RED, _("Red")),
        (GREEN, _("Green")),
    ]
    SCAN_LABEL_STEP = 10
    COPY_LABEL_STEP = 1
    color = models.CharField(
        max_length=1,
        choices=COLOR_CHOICES,
        default=BLACK,
    )
    CLASSIC = "CLASSIC"
    NTAG215 = "NTAG215"
    KIND_CHOICES = [
        (CLASSIC, _("MIFARE Classic")),
        (NTAG215, _("NTAG215")),
    ]
    kind = models.CharField(
        max_length=8,
        choices=KIND_CHOICES,
        default=CLASSIC,
    )
    BIG_ENDIAN = "BIG"
    LITTLE_ENDIAN = "LITTLE"
    ENDIANNESS_CHOICES = [
        (BIG_ENDIAN, _("Big endian")),
        (LITTLE_ENDIAN, _("Little endian")),
    ]
    endianness = models.CharField(
        max_length=6,
        choices=ENDIANNESS_CHOICES,
        default=BIG_ENDIAN,
    )
    reference = models.ForeignKey(
        "links.Reference",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="rfids",
        help_text="Optional reference for this RFID.",
    )
    origin_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_rfids",
        help_text="Node where this RFID record was created.",
    )
    released = models.BooleanField(default=False)
    added_on = models.DateTimeField(auto_now_add=True)
    last_seen_on = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if not self.origin_node_id:
            try:
                from apps.nodes.models import Node  # imported lazily to avoid circular import
            except Exception:  # pragma: no cover - nodes app may be unavailable
                node = None
            else:
                node = Node.get_local()
            if node:
                self.origin_node = node
                if update_fields:
                    fields = set(update_fields)
                    if "origin_node" not in fields:
                        fields.add("origin_node")
                        kwargs["update_fields"] = tuple(fields)
        if self.pk:
            old = type(self).objects.filter(pk=self.pk).values("key_a", "key_b").first()
            if old:
                if self.key_a and old["key_a"] != self.key_a.upper():
                    self.key_a_verified = False
                if self.key_b and old["key_b"] != self.key_b.upper():
                    self.key_b_verified = False
        if self.rfid:
            normalized_rfid = self.rfid.upper()
            self.rfid = normalized_rfid
            reversed_uid = self.reverse_uid(normalized_rfid)
            if reversed_uid != self.reversed_uid:
                self.reversed_uid = reversed_uid
                if update_fields:
                    fields = set(update_fields)
                    if "reversed_uid" not in fields:
                        fields.add("reversed_uid")
                        kwargs["update_fields"] = tuple(fields)
        if self.key_a:
            self.key_a = self.key_a.upper()
        if self.key_b:
            self.key_b = self.key_b.upper()
        if self.kind:
            self.kind = self.kind.upper()
        if self.endianness:
            self.endianness = self.normalize_endianness(self.endianness)
        super().save(*args, **kwargs)
        if not self.allowed:
            self.energy_accounts.clear()

    def __str__(self):  # pragma: no cover - simple representation
        return str(self.label_id)

    def qr_test_link(self) -> str:
        """Return a link that previews this RFID value as a QR code."""

        if not self.rfid:
            return ""
        qr = qrcode.QRCode(box_size=6, border=2)
        qr.add_data(self.rfid)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(
            "ascii"
        )
        return format_html(
            '<a href="{}" target="_blank" rel="noopener">{}</a>',
            data_uri,
            _("Open QR preview"),
        )

    qr_test_link.short_description = _("QR test link")

    @classmethod
    def normalize_code(cls, value: str) -> str:
        """Return ``value`` normalized for comparisons."""

        return "".join((value or "").split()).upper()

    def adopt_rfid(self, candidate: str) -> bool:
        """Adopt ``candidate`` as the stored RFID if it is a better match."""

        normalized = type(self).normalize_code(candidate)
        if not normalized:
            return False
        current = type(self).normalize_code(self.rfid)
        if current == normalized:
            return False
        if not current:
            self.rfid = normalized
            return True
        reversed_current = type(self).reverse_uid(current)
        if reversed_current and reversed_current == normalized:
            self.rfid = normalized
            return True
        if len(normalized) < len(current):
            self.rfid = normalized
            return True
        if len(normalized) == len(current) and normalized < current:
            self.rfid = normalized
            return True
        return False

    @classmethod
    def matching_queryset(cls, value: str) -> models.QuerySet["RFID"]:
        """Return RFID records matching ``value`` using prefix comparison."""

        normalized = cls.normalize_code(value)
        if not normalized:
            return cls.objects.none()

        conditions: list[Q] = []
        candidate = normalized
        if candidate:
            conditions.append(Q(rfid=candidate))
        alternate = cls.reverse_uid(candidate)
        if alternate and alternate != candidate:
            conditions.append(Q(rfid=alternate))

        prefix_length = min(len(candidate), cls.MATCH_PREFIX_LENGTH)
        if prefix_length:
            prefix = candidate[:prefix_length]
            conditions.append(Q(rfid__startswith=prefix))
            if alternate and alternate != candidate:
                alt_prefix = alternate[:prefix_length]
                if alt_prefix:
                    conditions.append(Q(rfid__startswith=alt_prefix))

        query: Q | None = None
        for condition in conditions:
            query = condition if query is None else query | condition

        if query is None:
            return cls.objects.none()

        queryset = cls.objects.filter(query).distinct()
        return queryset.annotate(rfid_length=Length("rfid")).order_by(
            "rfid_length", "rfid", "pk"
        )

    @classmethod
    def find_match(cls, value: str) -> "RFID | None":
        """Return the best matching RFID for ``value`` if it exists."""

        return cls.matching_queryset(value).first()

    @classmethod
    def update_or_create_from_code(
        cls, value: str, defaults: dict[str, Any] | None = None
    ) -> tuple["RFID", bool]:
        """Update or create an RFID using relaxed matching rules."""

        normalized = cls.normalize_code(value)
        if not normalized:
            raise ValueError("RFID value is required")

        defaults_map = defaults.copy() if defaults else {}
        existing = cls.find_match(normalized)
        if existing:
            update_fields: set[str] = set()
            if existing.adopt_rfid(normalized):
                update_fields.add("rfid")
            for field_name, new_value in defaults_map.items():
                if getattr(existing, field_name) != new_value:
                    setattr(existing, field_name, new_value)
                    update_fields.add(field_name)
            if update_fields:
                existing.save(update_fields=sorted(update_fields))
            return existing, False

        create_kwargs = defaults_map
        create_kwargs["rfid"] = normalized
        tag = cls.objects.create(**create_kwargs)
        return tag, True

    @classmethod
    def normalize_endianness(cls, value: object) -> str:
        """Return a valid endianness value, defaulting to BIG."""

        if isinstance(value, str):
            candidate = value.strip().upper()
            valid = {choice[0] for choice in cls.ENDIANNESS_CHOICES}
            if candidate in valid:
                return candidate
        return cls.BIG_ENDIAN

    @staticmethod
    def reverse_uid(value: str) -> str:
        """Return ``value`` with reversed byte order for reference storage."""

        normalized = "".join((value or "").split()).upper()
        if not normalized:
            return ""
        if len(normalized) % 2 != 0:
            return normalized[::-1]
        bytes_list = [normalized[index : index + 2] for index in range(0, len(normalized), 2)]
        bytes_list.reverse()
        return "".join(bytes_list)

    @classmethod
    def next_scan_label(
        cls, *, step: int | None = None, start: int | None = None
    ) -> int:
        """Return the next label id for RFID tags created by scanning."""

        step_value = step or cls.SCAN_LABEL_STEP
        if step_value <= 0:
            raise ValueError("step must be a positive integer")
        start_value = start if start is not None else step_value

        labels_qs = (
            cls.objects.order_by("-label_id").values_list("label_id", flat=True)
        )
        max_label = 0
        last_multiple = 0
        for value in labels_qs.iterator():
            if value is None:
                continue
            if max_label == 0:
                max_label = value
            if value >= start_value and value % step_value == 0:
                last_multiple = value
                break
        if last_multiple:
            candidate = last_multiple + step_value
        else:
            candidate = start_value
        if max_label:
            while candidate <= max_label:
                candidate += step_value
        return candidate

    @classmethod
    def next_copy_label(
        cls, source: "RFID", *, step: int | None = None
    ) -> int:
        """Return the next label id when copying ``source`` to a new card."""

        step_value = step or cls.COPY_LABEL_STEP
        if step_value <= 0:
            raise ValueError("step must be a positive integer")
        base_label = (source.label_id or 0) + step_value
        candidate = base_label if base_label > 0 else step_value
        while cls.objects.filter(label_id=candidate).exists():
            candidate += step_value
        return candidate

    @classmethod
    def _reset_label_sequence(cls) -> None:
        """Ensure the PK sequence is at or above the current max label id."""

        connection = connections[cls.objects.db]
        reset_sql = connection.ops.sequence_reset_sql(no_style(), [cls])
        if not reset_sql:
            return
        with connection.cursor() as cursor:
            for statement in reset_sql:
                cursor.execute(statement)

    @classmethod
    def register_scan(
        cls,
        rfid: str,
        *,
        kind: str | None = None,
        endianness: str | None = None,
    ) -> tuple["RFID", bool]:
        """Return or create an RFID that was detected via scanning."""

        normalized = cls.normalize_code(rfid)
        desired_endianness = cls.normalize_endianness(endianness)
        existing = cls.find_match(normalized)
        if existing:
            update_fields: list[str] = []
            if existing.adopt_rfid(normalized):
                update_fields.append("rfid")
            if existing.endianness != desired_endianness:
                existing.endianness = desired_endianness
                update_fields.append("endianness")
            if update_fields:
                existing.save(update_fields=update_fields)
            return existing, False

        attempts = 0
        max_attempts = 10
        while attempts < max_attempts:
            attempts += 1
            label_id = cls.next_scan_label()
            create_kwargs = {
                "label_id": label_id,
                "rfid": normalized,
                "allowed": True,
                "released": False,
                "endianness": desired_endianness,
            }
            if kind:
                create_kwargs["kind"] = kind
            try:
                with transaction.atomic():
                    tag = cls.objects.create(**create_kwargs)
                    cls._reset_label_sequence()
            except IntegrityError:
                existing = cls.find_match(normalized)
                if existing:
                    return existing, False
            else:
                return tag, True
        raise IntegrityError("Unable to allocate label id for scanned RFID")

    @classmethod
    def get_account_by_rfid(cls, value):
        """Return the customer account associated with an RFID code if it exists."""
        try:
            CustomerAccount = apps.get_model("energy", "CustomerAccount")
        except LookupError:  # pragma: no cover - energy app optional
            return None
        matches = cls.matching_queryset(value).filter(allowed=True)
        if not matches.exists():
            return None
        return (
            CustomerAccount.objects.filter(rfids__in=matches)
            .distinct()
            .first()
        )

    class Meta:
        verbose_name = "RFID"
        verbose_name_plural = "RFIDs"
        db_table = "core_rfid"




class TOTPDeviceSettings(Entity):
    """Per-device configuration options for authenticator enrollments."""

    device = models.OneToOneField(
        "otp_totp.TOTPDevice",
        on_delete=models.CASCADE,
        related_name="custom_settings",
    )
    issuer = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text=_("Label shown in authenticator apps. Leave blank to use Arthexis."),
    )
    allow_without_password = models.BooleanField(
        default=False,
        help_text=_("Allow authenticator logins to skip the password step."),
    )
    security_group = models.ForeignKey(
        "core.SecurityGroup",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="totp_devices",
        help_text=_(
            "Share this authenticator with every user in the selected security group."
        ),
    )
    class Meta:
        verbose_name = _("Authenticator Device Setting")
        verbose_name_plural = _("Authenticator Device Settings")


class PasskeyCredential(Entity):
    """Stored WebAuthn credentials that allow passwordless logins."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="passkeys",
    )
    name = models.CharField(
        max_length=80,
        help_text=_("Friendly label shown on the security settings page."),
    )
    credential_id = models.CharField(
        max_length=255,
        unique=True,
        help_text=_("Base64-encoded identifier returned by the authenticator."),
    )
    public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    user_handle = models.CharField(max_length=255)
    transports = models.JSONField(default=list, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name", "created_at")
        verbose_name = _("Passkey")
        verbose_name_plural = _("Passkeys")
        constraints = [
            models.UniqueConstraint(
                fields=("user", "name"),
                name="core_passkey_unique_name_per_user",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return f"{self.name} ({self.user})"


class AdminCommandResult(Entity):
    """Persisted output for ad-hoc Django management command runs."""

    command = models.TextField()
    resolved_command = models.TextField()
    command_name = models.CharField(max_length=150, blank=True)
    stdout = models.TextField(blank=True)
    stderr = models.TextField(blank=True)
    traceback = models.TextField(blank=True)
    runtime = models.DurationField(default=timedelta)
    exit_code = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_results",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        verbose_name = _("Command Result")
        verbose_name_plural = _("Command Results")

    def __str__(self) -> str:  # pragma: no cover - human-readable representation
        return self.command_name or self.command

# Backwards-compatible proxies for energy domain models
from apps.energy import models as energy_models
from apps.maps import models as map_models


class EnergyTariff(energy_models.EnergyTariff):
    class Meta(energy_models.EnergyTariff.Meta):
        proxy = True
        app_label = "core"


class Location(map_models.Location):
    class Meta(map_models.Location.Meta):
        proxy = True
        app_label = "core"


class CustomerAccount(energy_models.CustomerAccount):
    class Meta(energy_models.CustomerAccount.Meta):
        proxy = True
        app_label = "core"


class EnergyCredit(energy_models.EnergyCredit):
    class Meta(energy_models.EnergyCredit.Meta):
        proxy = True
        app_label = "core"


class EnergyTransaction(energy_models.EnergyTransaction):
    class Meta(energy_models.EnergyTransaction.Meta):
        proxy = True
        app_label = "core"


class ClientReportSchedule(energy_models.ClientReportSchedule):
    class Meta(energy_models.ClientReportSchedule.Meta):
        proxy = True
        app_label = "core"


class ClientReport(energy_models.ClientReport):
    class Meta(energy_models.ClientReport.Meta):
        proxy = True
        app_label = "core"
