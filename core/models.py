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
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.core.exceptions import ValidationError
from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.views.decorators.debug import sensitive_variables
from datetime import (
    time as datetime_time,
    timedelta,
    datetime as datetime_datetime,
    date as datetime_date,
    timezone as datetime_timezone,
)
import logging
import json
from decimal import Decimal
from django.contrib.contenttypes.models import ContentType
import hashlib
import hmac
import os
import subprocess
import re
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
from core.celery_utils import normalize_periodic_task_name
from typing import Any, Type
from defusedxml import xmlrpc as defused_xmlrpc
import requests

defused_xmlrpc.monkey_patch()
xmlrpc_client = defused_xmlrpc.xmlrpc_client

logger = logging.getLogger(__name__)


def _available_language_codes() -> set[str]:
    return {code.lower() for code, _ in getattr(settings, "LANGUAGES", [])}


def default_report_language() -> str:
    configured = getattr(settings, "LANGUAGE_CODE", "en") or "en"
    configured = configured.replace("_", "-").lower()
    base = configured.split("-", 1)[0]
    available = _available_language_codes()
    if base in available:
        return base
    if configured in available:
        return configured
    if available:
        return next(iter(sorted(available)))
    return "en"


def normalize_report_language(language: str | None) -> str:
    default = default_report_language()
    if not language:
        return default
    candidate = str(language).strip().lower()
    if not candidate:
        return default
    candidate = candidate.replace("_", "-")
    available = _available_language_codes()
    if candidate in available:
        return candidate
    base = candidate.split("-", 1)[0]
    if base in available:
        return base
    return default


def normalize_report_title(title: str | None) -> str:
    value = (title or "").strip()
    if "\r" in value or "\n" in value:
        raise ValidationError(
            _("Report title cannot contain control characters."),
        )
    return value


from .entity import Entity, EntityUserManager, EntityManager
from .release import (
    Package as ReleasePackage,
    Credentials,
    DEFAULT_PACKAGE,
    RepositoryTarget,
    GitCredentials,
)


def default_package_modules() -> list[str]:
    """Return the default package module list."""

    return list(DEFAULT_PACKAGE.packages)
from . import temp_passwords
from . import user_data  # noqa: F401 - ensure signal registration
from .fields import (
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


_SOCIAL_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}\Z)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$"
)


social_domain_validator = RegexValidator(
    regex=_SOCIAL_DOMAIN_PATTERN,
    message=_("Enter a valid domain name such as example.com."),
    code="invalid",
)


social_did_validator = RegexValidator(
    regex=r"^(|did:[a-z0-9]+:[A-Za-z0-9.\-_:]+)$",
    message=_("Enter a valid DID such as did:plc:1234abcd."),
    code="invalid",
)


class SigilRootManager(EntityManager):
    def get_by_natural_key(self, prefix: str):
        return self.get(prefix=prefix)


class SigilRoot(Entity):
    class Context(models.TextChoices):
        CONFIG = "config", "Configuration"
        ENTITY = "entity", "Entity"

    prefix = models.CharField(max_length=50, unique=True)
    context_type = models.CharField(max_length=20, choices=Context.choices)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.CASCADE
    )

    objects = SigilRootManager()

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.prefix

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.prefix,)

    class Meta:
        verbose_name = "Sigil Root"
        verbose_name_plural = "Sigil Roots"


class CustomSigil(SigilRoot):
    class Meta:
        proxy = True
        app_label = "pages"
        verbose_name = _("Custom Sigil")
        verbose_name_plural = _("Custom Sigils")


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
    ip_address = models.GenericIPAddressField(null=True, blank=True)
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
        "teams.EmailOutbox",
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


class PublicWifiAccess(Entity):
    """Represent a Wi-Fi lease granted to a client for internet access."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="public_wifi_accesses",
    )
    mac_address = models.CharField(max_length=17)
    created_on = models.DateTimeField(auto_now_add=True)
    updated_on = models.DateTimeField(auto_now=True)
    revoked_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("user", "mac_address")
        verbose_name = "Wi-Fi Lease"
        verbose_name_plural = "Wi-Fi Leases"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.user} -> {self.mac_address}"


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _revoke_public_wifi_when_inactive(sender, instance, **kwargs):
    if instance.is_active:
        return
    from core import public_wifi

    public_wifi.revoke_public_access_for_user(instance)


@receiver(post_delete, sender=settings.AUTH_USER_MODEL)
def _cleanup_public_wifi_on_delete(sender, instance, **kwargs):
    from core import public_wifi

    public_wifi.revoke_public_access_for_user(instance)


class User(Entity, AbstractUser):
    SYSTEM_USERNAME = "arthexis"
    ADMIN_USERNAME = "admin"
    PROFILE_RESTRICTED_USERNAMES = frozenset()

    objects = EntityUserManager()
    all_objects = DjangoUserManager()
    """Custom user model."""
    data_path = models.CharField(max_length=255, blank=True)
    last_visit_ip_address = models.GenericIPAddressField(null=True, blank=True)
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

    def _direct_profile(self, model_label: str):
        model = apps.get_model("core", model_label)
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
    def odoo_profile(self):
        return self._direct_profile("OdooProfile")

    @property
    def social_profile(self):
        return self._direct_profile("SocialProfile")

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


class OdooProfile(Profile):
    """Store Odoo API credentials for a user."""

    class CRM(models.TextChoices):
        ODOO = "odoo", _("Odoo")

    profile_fields = ("host", "database", "username", "password")
    host = SigilShortAutoField(max_length=255)
    database = SigilShortAutoField(max_length=255)
    username = SigilShortAutoField(max_length=255)
    password = SigilShortAutoField(max_length=255)
    crm = models.CharField(
        max_length=32,
        choices=CRM.choices,
        default=CRM.ODOO,
    )
    verified_on = models.DateTimeField(null=True, blank=True)
    odoo_uid = models.PositiveIntegerField(null=True, blank=True, editable=False)
    name = models.CharField(max_length=255, blank=True, editable=False)
    email = models.EmailField(blank=True, editable=False)

    def _clear_verification(self):
        self.verified_on = None
        self.odoo_uid = None
        self.name = ""
        self.email = ""

    def _resolved_field_value(self, field: str) -> str:
        """Return the resolved value for ``field`` falling back to raw data."""

        resolved = self.resolve_sigils(field)
        if resolved:
            return resolved
        value = getattr(self, field, "")
        return value or ""

    def _display_identifier(self) -> str:
        """Return the display label for this profile."""

        username = self._resolved_field_value("username")
        if username:
            return username
        database = self._resolved_field_value("database")
        return database or ""

    def _profile_name(self) -> str:
        """Return the stored name for this profile without database suffix."""

        username = self._resolved_field_value("username")
        if username:
            return username
        return self._resolved_field_value("database")

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).all_objects.get(pk=self.pk)
            if (
                old.username != self.username
                or old.password != self.password
                or old.database != self.database
                or old.host != self.host
            ):
                self._clear_verification()
        computed_name = self._profile_name()
        update_fields = kwargs.get("update_fields")
        update_fields_set = set(update_fields) if update_fields is not None else None
        if computed_name != self.name:
            self.name = computed_name
            if update_fields_set is not None:
                update_fields_set.add("name")
        if update_fields_set is not None:
            kwargs["update_fields"] = list(update_fields_set)
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.verified_on is not None

    def verify(self):
        """Check credentials against Odoo and pull user info."""
        common = xmlrpc_client.ServerProxy(f"{self.host}/xmlrpc/2/common")
        uid = common.authenticate(self.database, self.username, self.password, {})
        if not uid:
            self._clear_verification()
            raise ValidationError(_("Invalid Odoo credentials"))
        models_proxy = xmlrpc_client.ServerProxy(f"{self.host}/xmlrpc/2/object")
        info = models_proxy.execute_kw(
            self.database,
            uid,
            self.password,
            "res.users",
            "read",
            [uid],
            {"fields": ["name", "email"]},
        )[0]
        self.odoo_uid = uid
        self.email = info.get("email", "")
        self.verified_on = timezone.now()
        self.name = self._profile_name()
        self.save(update_fields=["odoo_uid", "name", "email", "verified_on"])
        return True

    def execute(self, model, method, *args, **kwargs):
        """Execute an Odoo RPC call, invalidating credentials on failure."""
        try:
            client = xmlrpc_client.ServerProxy(f"{self.host}/xmlrpc/2/object")
            call_args = list(args)
            call_kwargs = dict(kwargs)
            return client.execute_kw(
                self.database,
                self.odoo_uid,
                self.password,
                model,
                method,
                call_args,
                call_kwargs,
            )
        except Exception:
            logger.exception(
                "Odoo RPC %s.%s failed for profile %s (host=%s, database=%s, username=%s)",
                model,
                method,
                self.pk,
                self.host,
                self.database,
                self.username,
            )
            self._clear_verification()
            self.save(update_fields=["verified_on"])
            raise

    def __str__(self):  # pragma: no cover - simple representation
        label = self._display_identifier()
        if label:
            return label
        owner = self.owner_display()
        return f"{owner} @ {self.host}" if owner else self.host

    class Meta:
        verbose_name = _("CRM Employee")
        verbose_name_plural = _("CRM Employees")
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="odooprofile_requires_owner",
            )
        ]


class OpenPayProfile(Profile):
    """Store payment processor credentials for a user or security group."""

    PROCESSOR_OPENPAY = "openpay"
    PROCESSOR_PAYPAL = "paypal"
    PROCESSOR_CHOICES = (
        (PROCESSOR_OPENPAY, _("OpenPay")),
        (PROCESSOR_PAYPAL, _("PayPal")),
    )

    SANDBOX_API_URL = "https://sandbox-api.openpay.mx/v1"
    PRODUCTION_API_URL = "https://api.openpay.mx/v1"

    PAYPAL_SANDBOX_API_URL = "https://api-m.sandbox.paypal.com"
    PAYPAL_PRODUCTION_API_URL = "https://api-m.paypal.com"

    profile_fields = (
        "merchant_id",
        "private_key",
        "public_key",
        "is_production",
        "webhook_secret",
        "paypal_client_id",
        "paypal_client_secret",
        "paypal_webhook_id",
        "paypal_is_production",
    )

    default_processor = models.CharField(
        max_length=20,
        choices=PROCESSOR_CHOICES,
        default=PROCESSOR_OPENPAY,
    )
    merchant_id = SigilShortAutoField(max_length=100, blank=True)
    private_key = SigilShortAutoField(max_length=255, blank=True)
    public_key = SigilShortAutoField(max_length=255, blank=True)
    is_production = models.BooleanField(default=False)
    webhook_secret = SigilShortAutoField(max_length=255, blank=True)
    paypal_client_id = SigilShortAutoField(max_length=255, blank=True)
    paypal_client_secret = SigilShortAutoField(max_length=255, blank=True)
    paypal_webhook_id = SigilShortAutoField(max_length=255, blank=True)
    paypal_is_production = models.BooleanField(default=False)
    verified_on = models.DateTimeField(null=True, blank=True)
    verification_reference = models.CharField(max_length=255, blank=True, editable=False)

    def _clear_verification(self):
        self.verified_on = None
        self.verification_reference = ""

    def save(self, *args, **kwargs):
        if self.pk:
            old = type(self).all_objects.get(pk=self.pk)
            if (
                old.merchant_id != self.merchant_id
                or old.private_key != self.private_key
                or old.public_key != self.public_key
                or old.is_production != self.is_production
                or old.webhook_secret != self.webhook_secret
                or old.default_processor != self.default_processor
                or old.paypal_client_id != self.paypal_client_id
                or old.paypal_client_secret != self.paypal_client_secret
                or old.paypal_webhook_id != self.paypal_webhook_id
                or old.paypal_is_production != self.paypal_is_production
            ):
                self._clear_verification()
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.verified_on is not None

    # --- OpenPay helpers -------------------------------------------------

    def get_api_base_url(self) -> str:
        return self.PRODUCTION_API_URL if self.is_production else self.SANDBOX_API_URL

    def build_api_url(self, path: str = "") -> str:
        path = path.strip("/")
        base = self.get_api_base_url()
        if path:
            return f"{base}/{self.merchant_id}/{path}"
        return f"{base}/{self.merchant_id}"

    def get_auth(self) -> tuple[str, str]:
        return (self.private_key, "")

    def is_sandbox(self) -> bool:
        return not self.is_production

    # --- PayPal helpers --------------------------------------------------

    def get_paypal_api_base_url(self) -> str:
        return (
            self.PAYPAL_PRODUCTION_API_URL
            if self.paypal_is_production
            else self.PAYPAL_SANDBOX_API_URL
        )

    def get_paypal_auth(self) -> tuple[str, str]:
        return (self.paypal_client_id, self.paypal_client_secret)

    # --- Processor utilities --------------------------------------------

    def has_openpay_credentials(self) -> bool:
        return all(
            getattr(self, field)
            for field in ("merchant_id", "private_key", "public_key")
        )

    def has_paypal_credentials(self) -> bool:
        return all(
            getattr(self, field)
            for field in ("paypal_client_id", "paypal_client_secret")
        )

    def iter_processors(self):
        preferred = self.default_processor or self.PROCESSOR_OPENPAY
        ordered = [preferred]
        other = (
            self.PROCESSOR_PAYPAL
            if preferred == self.PROCESSOR_OPENPAY
            else self.PROCESSOR_OPENPAY
        )
        ordered.append(other)
        for processor in ordered:
            if processor == self.PROCESSOR_OPENPAY and self.has_openpay_credentials():
                yield processor
            elif processor == self.PROCESSOR_PAYPAL and self.has_paypal_credentials():
                yield processor

    def sign_webhook(self, payload: bytes | str, timestamp: str | None = None) -> str:
        if not self.webhook_secret:
            raise ValueError("Webhook secret is not configured")
        if isinstance(payload, str):
            payload_bytes = payload.encode("utf-8")
        else:
            payload_bytes = payload
        if timestamp:
            message = b".".join([timestamp.encode("utf-8"), payload_bytes])
        else:
            message = payload_bytes
        return hmac.new(
            self.webhook_secret.encode("utf-8"),
            message,
            hashlib.sha512,
        ).hexdigest()

    def use_production(self):
        self.is_production = True
        self._clear_verification()
        return self

    def use_sandbox(self):
        self.is_production = False
        self._clear_verification()
        return self

    def set_environment(self, *, production: bool):
        self.is_production = bool(production)
        self._clear_verification()
        return self

    def _verify_openpay(self):
        url = self.build_api_url("charges")
        try:
            response = requests.get(
                url,
                auth=self.get_auth(),
                params={"limit": 1},
                timeout=10,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(
                _("Unable to verify OpenPay credentials: %(error)s")
                % {"error": exc}
            ) from exc
        if response.status_code != 200:
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(_("Invalid OpenPay credentials"))
        try:
            payload = response.json() or {}
        except ValueError:
            payload = {}
        reference = ""
        if isinstance(payload, dict):
            reference = (
                payload.get("status")
                or payload.get("name")
                or payload.get("id")
                or payload.get("description")
                or ""
            )
        elif isinstance(payload, list) and payload:
            first = payload[0]
            if isinstance(first, dict):
                reference = (
                    first.get("status")
                    or first.get("id")
                    or first.get("description")
                    or ""
                )
        self.verification_reference = str(reference) if reference else ""
        self.verified_on = timezone.now()
        self.save(update_fields=["verification_reference", "verified_on"])
        return True

    def _verify_paypal(self):
        url = f"{self.get_paypal_api_base_url()}/v1/oauth2/token"
        try:
            response = requests.post(
                url,
                auth=self.get_paypal_auth(),
                data={"grant_type": "client_credentials"},
                timeout=10,
            )
        except requests.RequestException as exc:  # pragma: no cover - network failure
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(
                _("Unable to verify PayPal credentials: %(error)s")
                % {"error": exc}
            ) from exc
        if response.status_code != 200:
            self._clear_verification()
            if self.pk:
                self.save(update_fields=["verification_reference", "verified_on"])
            raise ValidationError(_("Invalid PayPal credentials"))
        try:
            payload = response.json() or {}
        except ValueError:
            payload = {}
        scope = ""
        if isinstance(payload, dict):
            scope = payload.get("scope") or payload.get("access_token") or ""
        self.verification_reference = f"PayPal: {scope}" if scope else "PayPal"
        self.verified_on = timezone.now()
        self.save(update_fields=["verification_reference", "verified_on"])
        return True

    def verify(self):
        errors = []
        for processor in self.iter_processors():
            try:
                if processor == self.PROCESSOR_OPENPAY:
                    return self._verify_openpay()
                if processor == self.PROCESSOR_PAYPAL:
                    return self._verify_paypal()
            except ValidationError as exc:
                errors.append(exc)
        if errors:
            raise errors[-1]
        raise ValidationError(_("No payment processors are configured."))

    def __str__(self):  # pragma: no cover - simple representation
        owner = self.owner_display()
        identifier = self.merchant_id or self.paypal_client_id or ""
        return f"{owner} @ {identifier}" if owner and identifier else (owner or identifier)

    class Meta:
        verbose_name = _("Payment Processor")
        verbose_name_plural = _("Payment Processors")
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="openpayprofile_requires_owner",
            )
        ]


class GoogleCalendarProfile(Profile):
    """Store Google Calendar configuration for a user or security group."""

    profile_fields = ("calendar_id", "api_key", "display_name", "timezone")

    calendar_id = SigilShortAutoField(max_length=255)
    api_key = SigilShortAutoField(max_length=255)
    display_name = models.CharField(max_length=255, blank=True)
    max_events = models.PositiveIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text=_("Number of upcoming events to display (1-20)."),
    )
    timezone = SigilShortAutoField(max_length=100, blank=True)

    GOOGLE_EVENTS_URL = (
        "https://www.googleapis.com/calendar/v3/calendars/{calendar}/events"
    )
    GOOGLE_EMBED_URL = "https://calendar.google.com/calendar/embed?src={calendar}&ctz={tz}"

    class Meta:
        verbose_name = _("Google Calendar")
        verbose_name_plural = _("Google Calendars")
        constraints = [
            models.CheckConstraint(
                check=(
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






class SocialProfile(Profile):
    """Store configuration required to link social accounts such as Bluesky."""

    class Network(models.TextChoices):
        BLUESKY = "bluesky", _("Bluesky")
        DISCORD = "discord", _("Discord")

    profile_fields = (
        "handle",
        "domain",
        "did",
        "application_id",
        "public_key",
        "guild_id",
        "bot_token",
        "default_channel_id",
    )

    network = models.CharField(
        max_length=32,
        choices=Network.choices,
        default=Network.BLUESKY,
        help_text=_(
            "Select the social network you want to connect. Bluesky and Discord are supported."
        ),
    )
    handle = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Bluesky handle that should resolve to Arthexis. Use the verified domain (for example arthexis.com)."
        ),
        validators=[social_domain_validator],
    )
    domain = models.CharField(
        max_length=253,
        blank=True,
        help_text=_(
            "Domain that hosts the Bluesky verification. Publish a _atproto TXT record or a /.well-known/atproto-did file with the DID below."
        ),
        validators=[social_domain_validator],
    )
    did = models.CharField(
        max_length=255,
        blank=True,
        help_text=_(
            "Optional DID that Bluesky assigns once the domain is linked (for example did:plc:1234abcd)."
        ),
        validators=[social_did_validator],
    )
    application_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord application ID used to control the bot."),
    )
    public_key = models.CharField(
        max_length=128,
        blank=True,
        help_text=_("Discord public key used to verify interaction requests."),
    )
    guild_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Discord guild (server) identifier where the bot should operate."),
    )
    bot_token = SigilShortAutoField(
        max_length=255,
        blank=True,
        help_text=_("Discord bot token required for authenticated actions."),
    )
    default_channel_id = models.CharField(
        max_length=32,
        blank=True,
        help_text=_("Optional Discord channel identifier used for default messaging."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.network == self.Network.DISCORD:
            if self.guild_id:
                return f"Discord guild {self.guild_id}"
            return "Discord"

        if self.network == self.Network.BLUESKY:
            if self.handle:
                return f"Bluesky @{self.handle}"
            if self.domain:
                return f"Bluesky @{self.domain}"

        network = dict(self.Network.choices).get(self.network)
        if network:
            return network

        owner = self.owner_display()
        return owner or super().__str__()

    class Meta:
        verbose_name = _("Social Identity")
        verbose_name_plural = _("Social Identities")
        constraints = [
            models.UniqueConstraint(
                fields=["network", "handle"],
                condition=~Q(handle=""),
                name="socialprofile_network_handle",
            ),
            models.UniqueConstraint(
                fields=["network", "domain"],
                condition=~Q(domain=""),
                name="socialprofile_network_domain",
            ),
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="socialprofile_requires_owner",
            ),
        ]


class EmailArtifact(Entity):
    """Store messages discovered by :class:`EmailCollector`."""

    collector = models.ForeignKey(
        "teams.EmailCollector", related_name="artifacts", on_delete=models.CASCADE
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
        "teams.EmailCollector",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Collector that discovered this message, if applicable.",
    )
    inbox = models.ForeignKey(
        "teams.EmailInbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Inbox account the message was read from or will use for sending.",
    )
    outbox = models.ForeignKey(
        "teams.EmailOutbox",
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


class ReferenceManager(EntityManager):
    def get_by_natural_key(self, alt_text: str):
        return self.get(alt_text=alt_text)


class Reference(Entity):
    """Store a piece of reference content which can be text or an image."""

    TEXT = "text"
    IMAGE = "image"
    CONTENT_TYPE_CHOICES = [
        (TEXT, "Text"),
        (IMAGE, "Image"),
    ]

    content_type = models.CharField(
        max_length=5, choices=CONTENT_TYPE_CHOICES, default=TEXT
    )
    alt_text = models.CharField("Title / Alt Text", max_length=500)
    value = models.TextField(blank=True)
    file = models.FileField(upload_to="refs/", blank=True)
    image = models.ImageField(upload_to="refs/qr/", blank=True)
    uses = models.PositiveIntegerField(default=0)
    method = models.CharField(max_length=50, default="qr")
    include_in_footer = models.BooleanField(
        default=False, verbose_name="Include in Footer"
    )
    show_in_header = models.BooleanField(
        default=False, verbose_name="Show in Header"
    )
    FOOTER_PUBLIC = "public"
    FOOTER_PRIVATE = "private"
    FOOTER_STAFF = "staff"
    FOOTER_VISIBILITY_CHOICES = [
        (FOOTER_PUBLIC, "Public"),
        (FOOTER_PRIVATE, "Private"),
        (FOOTER_STAFF, "Staff"),
    ]
    footer_visibility = models.CharField(
        max_length=7,
        choices=FOOTER_VISIBILITY_CHOICES,
        default=FOOTER_PUBLIC,
        verbose_name="Footer visibility",
    )
    transaction_uuid = models.UUIDField(
        default=uuid.uuid4,
        editable=True,
        db_index=True,
        verbose_name="transaction UUID",
    )
    created = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="references",
        null=True,
        blank=True,
    )
    sites = models.ManyToManyField(
        "sites.Site",
        blank=True,
        related_name="references",
    )
    roles = models.ManyToManyField(
        "nodes.NodeRole",
        blank=True,
        related_name="references",
    )
    features = models.ManyToManyField(
        "nodes.NodeFeature",
        blank=True,
        related_name="references",
    )

    objects = ReferenceManager()

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            if original.transaction_uuid != self.transaction_uuid:
                raise ValidationError(
                    {"transaction_uuid": "Cannot modify transaction UUID"}
                )
        if not self.image and self.value:
            qr = qrcode.QRCode(box_size=10, border=4)
            qr.add_data(self.value)
            qr.make(fit=True)
            img = qr.make_image(fill_color="black", back_color="white")
            buffer = BytesIO()
            img.save(buffer, format="PNG")
            filename = hashlib.sha256(self.value.encode()).hexdigest()[:16] + ".png"
            self.image.save(filename, ContentFile(buffer.getvalue()), save=False)
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.alt_text

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.alt_text,)


    class Meta:
        verbose_name = _("Reference")
        verbose_name_plural = _("References")


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
        "Reference",
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
                from nodes.models import Node  # imported lazily to avoid circular import
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
            CustomerAccount = apps.get_model("core", "CustomerAccount")
        except LookupError:  # pragma: no cover - customer accounts app optional
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


class EnergyTariffManager(EntityManager):
    def get_by_natural_key(
        self,
        year: int,
        season: str,
        zone: str,
        contract_type: str,
        period: str,
        unit: str,
        start_time,
        end_time,
    ):
        if isinstance(start_time, str):
            start_time = datetime_time.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime_time.fromisoformat(end_time)
        return self.get(
            year=year,
            season=season,
            zone=zone,
            contract_type=contract_type,
            period=period,
            unit=unit,
            start_time=start_time,
            end_time=end_time,
        )


class EnergyTariff(Entity):
    class Zone(models.TextChoices):
        ONE = "1", _("Zone 1")
        ONE_A = "1A", _("Zone 1A")
        ONE_B = "1B", _("Zone 1B")
        ONE_C = "1C", _("Zone 1C")
        ONE_D = "1D", _("Zone 1D")
        ONE_E = "1E", _("Zone 1E")
        ONE_F = "1F", _("Zone 1F")

    class Season(models.TextChoices):
        ANNUAL = "annual", _("All year")
        SUMMER = "summer", _("Summer season")
        NON_SUMMER = "non_summer", _("Non-summer season")

    class Period(models.TextChoices):
        FLAT = "flat", _("Flat rate")
        BASIC = "basic", _("Basic block")
        INTERMEDIATE_1 = "intermediate_1", _("Intermediate block 1")
        INTERMEDIATE_2 = "intermediate_2", _("Intermediate block 2")
        EXCESS = "excess", _("Excess consumption")
        BASE = "base", _("Base")
        INTERMEDIATE = "intermediate", _("Intermediate")
        PEAK = "peak", _("Peak")
        CRITICAL_PEAK = "critical_peak", _("Critical peak")
        DEMAND = "demand", _("Demand charge")
        CAPACITY = "capacity", _("Capacity charge")
        DISTRIBUTION = "distribution", _("Distribution charge")
        FIXED = "fixed", _("Fixed charge")

    class ContractType(models.TextChoices):
        DOMESTIC = "domestic", _("Domestic service (Tarifa 1)")
        DAC = "dac", _("High consumption domestic (DAC)")
        PDBT = "pdbt", _("General service low demand (PDBT)")
        GDBT = "gdbt", _("General service high demand (GDBT)")
        GDMTO = "gdmto", _("General distribution medium tension (GDMTO)")
        GDMTH = "gdmth", _("General distribution medium tension hourly (GDMTH)")

    class Unit(models.TextChoices):
        KWH = "kwh", _("Kilowatt-hour")
        KW = "kw", _("Kilowatt")
        MONTH = "month", _("Monthly charge")

    year = models.PositiveIntegerField(
        validators=[MinValueValidator(2000)],
        help_text=_("Calendar year when the tariff applies."),
    )
    season = models.CharField(
        max_length=16,
        choices=Season.choices,
        default=Season.ANNUAL,
        help_text=_("Season or applicability window defined by CFE."),
    )
    zone = models.CharField(
        max_length=3,
        choices=Zone.choices,
        help_text=_("CFE climate zone associated with the tariff."),
    )
    contract_type = models.CharField(
        max_length=16,
        choices=ContractType.choices,
        help_text=_("Type of service contract regulated by CFE."),
    )
    period = models.CharField(
        max_length=32,
        choices=Period.choices,
        help_text=_("Tariff block, demand component, or time-of-use period."),
    )
    unit = models.CharField(
        max_length=16,
        choices=Unit.choices,
        default=Unit.KWH,
        help_text=_("Measurement unit for the tariff charge."),
    )
    start_time = models.TimeField(
        help_text=_("Start time for the tariff's applicability window."),
    )
    end_time = models.TimeField(
        help_text=_("End time for the tariff's applicability window."),
    )
    price_mxn = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text=_("Customer price per unit in MXN."),
    )
    cost_mxn = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        help_text=_("Provider cost per unit in MXN."),
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text=_("Context or special billing conditions published by CFE."),
    )

    objects = EnergyTariffManager()

    class Meta:
        verbose_name = _("Energy Tariff")
        verbose_name_plural = _("Energy Tariffs")
        ordering = (
            "-year",
            "season",
            "zone",
            "contract_type",
            "period",
            "start_time",
        )
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "year",
                    "season",
                    "zone",
                    "contract_type",
                    "period",
                    "unit",
                    "start_time",
                    "end_time",
                ],
                name="uniq_energy_tariff_schedule",
            )
        ]
        indexes = [
            models.Index(
                fields=["year", "season", "zone", "contract_type"],
                name="energy_tariff_scope_idx",
            )
        ]

    def clean(self):
        super().clean()
        if self.start_time >= self.end_time:
            raise ValidationError(
                {"end_time": _("End time must be after the start time.")}
            )

    def __str__(self):  # pragma: no cover - simple representation
        return _("%(contract)s %(zone)s %(season)s %(year)s (%(period)s)") % {
            "contract": self.get_contract_type_display(),
            "zone": self.zone,
            "season": self.get_season_display(),
            "year": self.year,
            "period": self.get_period_display(),
        }

    def natural_key(self):  # pragma: no cover - simple representation
        return (
            self.year,
            self.season,
            self.zone,
            self.contract_type,
            self.period,
            self.unit,
            self.start_time.isoformat(),
            self.end_time.isoformat(),
        )

    natural_key.dependencies = []  # type: ignore[attr-defined]


class Location(Entity):
    """Physical location available for business operations."""

    name = models.CharField(max_length=200)
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
    )
    zone = models.CharField(
        max_length=3,
        choices=EnergyTariff.Zone.choices,
        blank=True,
        null=True,
        help_text=_("CFE climate zone used to select matching energy tariffs."),
    )
    contract_type = models.CharField(
        max_length=16,
        choices=EnergyTariff.ContractType.choices,
        blank=True,
        null=True,
        help_text=_("CFE service contract type required to match energy tariff pricing."),
    )
    address_line1 = models.CharField(
        _("Street address"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Primary street address or location description."),
    )
    address_line2 = models.CharField(
        _("Street address line 2"),
        max_length=255,
        blank=True,
        default="",
        help_text=_("Additional address information such as suite or building."),
    )
    city = models.CharField(
        _("City"),
        max_length=128,
        blank=True,
        default="",
    )
    state = models.CharField(
        _("State / Province"),
        max_length=128,
        blank=True,
        default="",
    )
    postal_code = models.CharField(
        _("Postal code"),
        max_length=32,
        blank=True,
        default="",
    )
    country = models.CharField(
        _("Country"),
        max_length=64,
        blank=True,
        default="",
    )
    phone_number = models.CharField(
        _("Phone number"),
        max_length=32,
        blank=True,
        default="",
    )
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_locations",
        verbose_name=_("Assigned to"),
        help_text=_("Optional user responsible for this location."),
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = _("Location")
        verbose_name_plural = _("Locations")
        db_table = "core_location"


class CustomerAccount(Entity):
    """Track kW energy credits, balance, and billing for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="customer_account",
        null=True,
        blank=True,
    )
    rfids = models.ManyToManyField(
        "RFID",
        blank=True,
        related_name="energy_accounts",
        db_table="core_account_rfids",
        verbose_name="RFIDs",
    )
    service_account = models.BooleanField(
        default=False,
        help_text="Allow transactions even when the balance is zero or negative",
    )
    balance_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Available currency balance for auto top-ups.",
    )
    minimum_purchase_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0"),
        help_text="Default amount to purchase when topping up via credit card.",
    )
    energy_tariff = models.ForeignKey(
        "EnergyTariff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accounts",
        help_text="Tariff used to convert currency balance to energy credits.",
    )
    credit_card_brand = models.CharField(
        max_length=20,
        blank=True,
        default="",
        help_text="Brand of the backup credit card.",
    )
    credit_card_last4 = models.CharField(
        max_length=4,
        blank=True,
        default="",
        help_text="Last four digits of the backup credit card.",
    )
    credit_card_exp_month = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(12)],
        help_text="Expiration month for the backup credit card.",
    )
    credit_card_exp_year = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Expiration year for the backup credit card.",
    )
    live_subscription_product = models.ForeignKey(
        "Product",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="live_subscription_accounts",
    )
    live_subscription_start_date = models.DateField(null=True, blank=True)
    live_subscription_next_renewal = models.DateField(null=True, blank=True)

    def can_authorize(self) -> bool:
        """Return True if this account should be authorized for charging."""
        if self.service_account:
            return True
        if self.balance_kw > 0:
            return True
        potential = self.potential_purchase_kw
        return potential > 0

    @property
    def credits_kw(self):
        """Total kW energy credits added to the customer account."""
        from django.db.models import Sum
        from decimal import Decimal

        total = self.credits.aggregate(total=Sum("amount_kw"))["total"]
        return total if total is not None else Decimal("0")

    @property
    def total_kw_spent(self):
        """Total kW consumed across all transactions."""
        from django.db.models import F, Sum, ExpressionWrapper, FloatField
        from decimal import Decimal

        expr = ExpressionWrapper(
            F("meter_stop") - F("meter_start"), output_field=FloatField()
        )
        total = self.transactions.filter(
            meter_start__isnull=False, meter_stop__isnull=False
        ).aggregate(total=Sum(expr))["total"]
        if total is None:
            return Decimal("0")
        return Decimal(str(total))

    @property
    def balance_kw(self):
        """Remaining kW available for the customer account."""
        return self.credits_kw - self.total_kw_spent

    @property
    def potential_purchase_kw(self):
        """kW that could be purchased using the current balance and tariff."""
        if not self.energy_tariff:
            return Decimal("0")
        price = self.energy_tariff.price_mxn
        if price is None or price <= 0:
            return Decimal("0")
        if self.balance_mxn <= 0:
            return Decimal("0")
        return self.balance_mxn / price

    def save(self, *args, **kwargs):
        if self.name:
            self.name = self.name.upper()
        if self.live_subscription_product and not self.live_subscription_start_date:
            self.live_subscription_start_date = timezone.now().date()
        if (
            self.live_subscription_product
            and self.live_subscription_start_date
            and not self.live_subscription_next_renewal
        ):
            self.live_subscription_next_renewal = (
                self.live_subscription_start_date
                + timedelta(days=self.live_subscription_product.renewal_period)
            )
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        return self.name

    class Meta:
        verbose_name = "Customer Account"
        verbose_name_plural = "Customer Accounts"
        db_table = "core_account"


class EnergyCredit(Entity):
    """Energy credits added to a customer account."""

    account = models.ForeignKey(
        CustomerAccount, on_delete=models.CASCADE, related_name="credits"
    )
    amount_kw = models.DecimalField(
        max_digits=10, decimal_places=2, verbose_name="Energy (kW)"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="credit_entries",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        user = (
            self.account.user
            if self.account.user
            else f"Customer Account {self.account_id}"
        )
        return f"{self.amount_kw} kW for {user}"

    class Meta:
        verbose_name = "Energy Credit"
        verbose_name_plural = "Energy Credits"
        db_table = "core_credit"


class EnergyTransaction(Entity):
    """Record of currency-to-energy purchases for an account."""

    account = models.ForeignKey(
        CustomerAccount, on_delete=models.CASCADE, related_name="energy_transactions"
    )
    tariff = models.ForeignKey(
        "EnergyTariff",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="energy_transactions",
        help_text="Tariff in effect when the purchase occurred.",
    )
    purchased_kw = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        help_text="Number of kW purchased for the account.",
    )
    charged_amount_mxn = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Currency amount used for the purchase.",
    )
    conversion_factor = models.DecimalField(
        max_digits=12,
        decimal_places=6,
        help_text="Conversion factor (kW per MXN) applied at purchase time.",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Energy Transaction"
        verbose_name_plural = "Energy Transactions"
        ordering = ("-created_on",)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.purchased_kw} kW on {self.created_on:%Y-%m-%d}"


class ClientReportSchedule(Entity):
    """Configuration for recurring :class:`ClientReport` generation."""

    PERIODICITY_NONE = "none"
    PERIODICITY_DAILY = "daily"
    PERIODICITY_WEEKLY = "weekly"
    PERIODICITY_MONTHLY = "monthly"
    PERIODICITY_CHOICES = [
        (PERIODICITY_NONE, "One-time"),
        (PERIODICITY_DAILY, "Daily"),
        (PERIODICITY_WEEKLY, "Weekly"),
        (PERIODICITY_MONTHLY, "Monthly"),
    ]

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedules",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_client_report_schedules",
    )
    periodicity = models.CharField(
        max_length=12, choices=PERIODICITY_CHOICES, default=PERIODICITY_NONE
    )
    language = models.CharField(
        max_length=12,
        choices=settings.LANGUAGES,
        default=default_report_language,
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Title"),
    )
    email_recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_report_schedules",
    )
    periodic_task = models.OneToOneField(
        "django_celery_beat.PeriodicTask",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_report_schedule",
    )
    last_generated_on = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Client Report Schedule"
        verbose_name_plural = "Client Report Schedules"

    @classmethod
    def label_for_periodicity(cls, value: str) -> str:
        lookup = dict(cls.PERIODICITY_CHOICES)
        return lookup.get(value, value)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        owner = self.owner.get_username() if self.owner else "Unassigned"
        return f"Client Report Schedule ({owner})"

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = normalize_report_title(self.title)
        sync = kwargs.pop("sync_task", True)
        super().save(*args, **kwargs)
        if sync and self.pk:
            self.sync_periodic_task()

    def delete(self, using=None, keep_parents=False):
        task_id = self.periodic_task_id
        super().delete(using=using, keep_parents=keep_parents)
        if task_id:
            from django_celery_beat.models import PeriodicTask

            PeriodicTask.objects.filter(pk=task_id).delete()

    def sync_periodic_task(self):
        """Ensure the Celery beat schedule matches the configured periodicity."""

        from django_celery_beat.models import CrontabSchedule, PeriodicTask
        from django.db import transaction
        import json as _json

        if self.periodicity == self.PERIODICITY_NONE:
            if self.periodic_task_id:
                PeriodicTask.objects.filter(pk=self.periodic_task_id).delete()
                type(self).objects.filter(pk=self.pk).update(periodic_task=None)
            return

        if self.periodicity == self.PERIODICITY_DAILY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="2",
                day_of_week="*",
                day_of_month="*",
                month_of_year="*",
            )
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="3",
                day_of_week="1",
                day_of_month="*",
                month_of_year="*",
            )
        else:
            schedule, _ = CrontabSchedule.objects.get_or_create(
                minute="0",
                hour="4",
                day_of_week="*",
                day_of_month="1",
                month_of_year="*",
            )

        raw_name = f"client_report_schedule_{self.pk}"
        name = normalize_periodic_task_name(PeriodicTask.objects, raw_name)
        defaults = {
            "crontab": schedule,
            "task": "core.tasks.run_client_report_schedule",
            "kwargs": _json.dumps({"schedule_id": self.pk}),
            "enabled": True,
        }
        with transaction.atomic():
            periodic_task, _ = PeriodicTask.objects.update_or_create(
                name=name, defaults=defaults
            )
            if self.periodic_task_id != periodic_task.pk:
                type(self).objects.filter(pk=self.pk).update(
                    periodic_task=periodic_task
                )

    def calculate_period(self, reference=None):
        """Return the date range covered for the next execution."""

        from django.utils import timezone
        import datetime as _datetime

        ref_date = reference or timezone.localdate()

        if self.periodicity == self.PERIODICITY_DAILY:
            end = ref_date - _datetime.timedelta(days=1)
            start = end
        elif self.periodicity == self.PERIODICITY_WEEKLY:
            start_of_week = ref_date - _datetime.timedelta(days=ref_date.weekday())
            end = start_of_week - _datetime.timedelta(days=1)
            start = end - _datetime.timedelta(days=6)
        elif self.periodicity == self.PERIODICITY_MONTHLY:
            first_of_month = ref_date.replace(day=1)
            end = first_of_month - _datetime.timedelta(days=1)
            start = end.replace(day=1)
        else:
            raise ValueError("calculate_period called for non-recurring schedule")

        return start, end

    def _advance_period(
        self, start: datetime_date, end: datetime_date
    ) -> tuple[datetime_date, datetime_date]:
        import calendar as _calendar
        import datetime as _datetime

        if self.periodicity == self.PERIODICITY_DAILY:
            delta = _datetime.timedelta(days=1)
            return start + delta, end + delta
        if self.periodicity == self.PERIODICITY_WEEKLY:
            delta = _datetime.timedelta(days=7)
            return start + delta, end + delta
        if self.periodicity == self.PERIODICITY_MONTHLY:
            base_start = start.replace(day=1)
            year = base_start.year
            month = base_start.month
            if month == 12:
                next_year = year + 1
                next_month = 1
            else:
                next_year = year
                next_month = month + 1
            next_start = base_start.replace(year=next_year, month=next_month, day=1)
            last_day = _calendar.monthrange(next_year, next_month)[1]
            next_end = next_start.replace(day=last_day)
            return next_start, next_end
        raise ValueError("advance_period called for non-recurring schedule")

    def iter_pending_periods(self, reference=None):
        from django.utils import timezone

        if self.periodicity == self.PERIODICITY_NONE:
            return []

        ref_date = reference or timezone.localdate()
        try:
            target_start, target_end = self.calculate_period(reference=ref_date)
        except ValueError:
            return []

        reports = self.reports.order_by("start_date", "end_date")
        last_report = reports.last()
        if last_report:
            current_start, current_end = self._advance_period(
                last_report.start_date, last_report.end_date
            )
        else:
            current_start, current_end = target_start, target_end

        if current_end < current_start:
            return []

        pending: list[tuple[datetime.date, datetime.date]] = []
        safety = 0
        while current_end <= target_end:
            exists = reports.filter(
                start_date=current_start, end_date=current_end
            ).exists()
            if not exists:
                pending.append((current_start, current_end))
            try:
                current_start, current_end = self._advance_period(
                    current_start, current_end
                )
            except ValueError:
                break
            safety += 1
            if safety > 400:
                break

        return pending

    def resolve_recipients(self):
        """Return (to, cc) email lists respecting owner fallbacks."""

        from django.contrib.auth import get_user_model

        to: list[str] = []
        cc: list[str] = []
        seen: set[str] = set()

        for email in self.email_recipients:
            normalized = (email or "").strip()
            if not normalized:
                continue
            if normalized.lower() in seen:
                continue
            to.append(normalized)
            seen.add(normalized.lower())

        owner_email = None
        if self.owner and self.owner.email:
            candidate = self.owner.email.strip()
            if candidate:
                owner_email = candidate

        if to:
            if owner_email and owner_email.lower() not in seen:
                cc.append(owner_email)
        else:
            if owner_email:
                to.append(owner_email)
                seen.add(owner_email.lower())
            else:
                admin_email = (
                    get_user_model()
                    .objects.filter(is_superuser=True, is_active=True)
                    .exclude(email="")
                    .values_list("email", flat=True)
                    .first()
                )
                if admin_email:
                    to.append(admin_email)
                    seen.add(admin_email.lower())
                elif settings.DEFAULT_FROM_EMAIL:
                    to.append(settings.DEFAULT_FROM_EMAIL)

        return to, cc

    def resolve_reply_to(self) -> list[str]:
        return ClientReport.resolve_reply_to_for_owner(self.owner)

    def get_outbox(self):
        """Return the preferred :class:`teams.models.EmailOutbox` instance."""

        return ClientReport.resolve_outbox_for_owner(self.owner)

    def notify_failure(self, message: str):
        from nodes.models import NetMessage

        NetMessage.broadcast("Client report delivery issue", message)

    def run(self, *, start: datetime_date | None = None, end: datetime_date | None = None):
        """Generate the report, persist it and deliver notifications."""

        if start is None or end is None:
            try:
                start, end = self.calculate_period()
            except ValueError:
                return None

        try:
            report = ClientReport.generate(
                start,
                end,
                owner=self.owner,
                schedule=self,
                recipients=self.email_recipients,
                disable_emails=self.disable_emails,
                chargers=list(self.chargers.all()),
                language=self.language,
                title=self.title,
            )
            report.chargers.set(self.chargers.all())
            report.store_local_copy()
        except Exception as exc:
            self.notify_failure(str(exc))
            raise

        if not self.disable_emails:
            to, cc = self.resolve_recipients()
            if not to:
                self.notify_failure("No recipients available for client report")
                raise RuntimeError("No recipients available for client report")
            else:
                try:
                    delivered = report.send_delivery(
                        to=to,
                        cc=cc,
                        outbox=self.get_outbox(),
                        reply_to=self.resolve_reply_to(),
                    )
                    if delivered:
                        type(report).objects.filter(pk=report.pk).update(
                            recipients=delivered
                        )
                        report.recipients = delivered
                except Exception as exc:
                    self.notify_failure(str(exc))
                    raise

        now = timezone.now()
        type(self).objects.filter(pk=self.pk).update(last_generated_on=now)
        self.last_generated_on = now
        return report

    def generate_missing_reports(self, reference=None):
        generated: list["ClientReport"] = []
        for start, end in self.iter_pending_periods(reference=reference):
            report = self.run(start=start, end=end)
            if report:
                generated.append(report)
        return generated


class ClientReport(Entity):
    """Snapshot of energy usage over a period."""

    start_date = models.DateField()
    end_date = models.DateField()
    created_on = models.DateTimeField(auto_now_add=True)
    data = models.JSONField(default=dict)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="client_reports",
    )
    schedule = models.ForeignKey(
        "ClientReportSchedule",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
    )
    language = models.CharField(
        max_length=12,
        choices=settings.LANGUAGES,
        default=default_report_language,
    )
    title = models.CharField(
        max_length=200,
        blank=True,
        default="",
        verbose_name=_("Title"),
    )
    recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
    chargers = models.ManyToManyField(
        "ocpp.Charger",
        blank=True,
        related_name="client_reports",
    )

    class Meta:
        verbose_name = _("Consumer Report")
        verbose_name_plural = _("Consumer Reports")
        db_table = "core_client_report"
        ordering = ["-created_on"]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        period_type = (
            self.schedule.periodicity
            if self.schedule
            else ClientReportSchedule.PERIODICITY_NONE
        )
        return f"{self.start_date} - {self.end_date} ({period_type})"

    @staticmethod
    def default_language() -> str:
        return default_report_language()

    @staticmethod
    def normalize_language(language: str | None) -> str:
        return normalize_report_language(language)

    @staticmethod
    def normalize_title(title: str | None) -> str:
        return normalize_report_title(title)

    def save(self, *args, **kwargs):
        if self.language:
            self.language = normalize_report_language(self.language)
        self.title = self.normalize_title(self.title)
        super().save(*args, **kwargs)

    @property
    def periodicity_label(self) -> str:
        if self.schedule:
            return self.schedule.get_periodicity_display()
        return ClientReportSchedule.label_for_periodicity(
            ClientReportSchedule.PERIODICITY_NONE
        )

    @property
    def total_kw_period(self) -> float:
        totals = (self.rows_for_display or {}).get("totals", {})
        return float(totals.get("total_kw_period", 0.0) or 0.0)

    @classmethod
    def generate(
        cls,
        start_date,
        end_date,
        *,
        owner=None,
        schedule=None,
        recipients: list[str] | None = None,
        disable_emails: bool = False,
        chargers=None,
        language: str | None = None,
        title: str | None = None,
    ):
        from collections.abc import Iterable as _Iterable

        charger_list = []
        if chargers:
            if isinstance(chargers, _Iterable):
                charger_list = list(chargers)
            else:
                charger_list = [chargers]

        payload = cls.build_rows(start_date, end_date, chargers=charger_list)
        normalized_language = cls.normalize_language(language)
        title_value = cls.normalize_title(title)
        report = cls.objects.create(
            start_date=start_date,
            end_date=end_date,
            data=payload,
            owner=owner,
            schedule=schedule,
            recipients=list(recipients or []),
            disable_emails=disable_emails,
            language=normalized_language,
            title=title_value,
        )
        if charger_list:
            report.chargers.set(charger_list)
        return report

    def store_local_copy(self, html: str | None = None):
        """Persist the report data and optional HTML rendering to disk."""

        import json as _json
        from django.template.loader import render_to_string

        base_dir = Path(settings.BASE_DIR)
        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"

        language_code = self.normalize_language(self.language)
        context = {
            "report": self,
            "language_code": language_code,
            "default_language": type(self).default_language(),
        }
        with override(language_code):
            html_content = html or render_to_string(
                "core/reports/client_report_email.html", context
            )
        html_path = report_dir / f"{identifier}.html"
        html_path.write_text(html_content, encoding="utf-8")

        json_path = report_dir / f"{identifier}.json"
        json_path.write_text(
            _json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export = {
            "html_path": ClientReport._relative_to_base(html_path, base_dir),
            "json_path": ClientReport._relative_to_base(json_path, base_dir),
            "pdf_path": ClientReport._relative_to_base(pdf_path, base_dir),
        }

        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return export, html_content

    def send_delivery(
        self,
        *,
        to: list[str] | tuple[str, ...],
        cc: list[str] | tuple[str, ...] | None = None,
        outbox=None,
        reply_to: list[str] | None = None,
    ) -> list[str]:
        from core import mailer

        recipients = list(to or [])
        if not recipients:
            return []

        pdf_path = self.ensure_pdf()
        attachments = [
            (pdf_path.name, pdf_path.read_bytes(), "application/pdf"),
        ]

        language_code = self.normalize_language(self.language)
        with override(language_code):
            totals = self.rows_for_display.get("totals", {})
            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            total_kw_period_label = gettext("Total kW during period")
            total_kw_all_label = gettext("Total kW (all time)")
            report_title = self.normalize_title(self.title) or gettext(
                "Consumer Report"
            )
            body_lines = [
                gettext("%(title)s for %(start)s through %(end)s.")
                % {"title": report_title, "start": start_display, "end": end_display},
                f"{total_kw_period_label}: "
                f"{formats.number_format(totals.get('total_kw_period', 0.0), decimal_pos=2, use_l10n=True)}.",
                f"{total_kw_all_label}: "
                f"{formats.number_format(totals.get('total_kw', 0.0), decimal_pos=2, use_l10n=True)}.",
            ]
            message = "\n".join(body_lines)
            subject = gettext("%(title)s %(start)s - %(end)s") % {
                "title": report_title,
                "start": start_display,
                "end": end_display,
            }

        kwargs = {}
        if reply_to:
            kwargs["reply_to"] = reply_to

        mailer.send(
            subject,
            message,
            recipients,
            outbox=outbox,
            cc=list(cc or []),
            attachments=attachments,
            **kwargs,
        )

        delivered = list(dict.fromkeys(recipients + list(cc or [])))
        return delivered

    @staticmethod
    def build_rows(
        start_date=None,
        end_date=None,
        *,
        for_display: bool = False,
        chargers=None,
    ):
        dataset = ClientReport._build_dataset(start_date, end_date, chargers=chargers)
        if for_display:
            return ClientReport._normalize_dataset_for_display(dataset)
        return dataset

    @staticmethod
    def _build_dataset(start_date=None, end_date=None, *, chargers=None):
        from datetime import datetime, time, timedelta, timezone as pytimezone
        from ocpp.models import (
            Charger,
            Transaction,
            annotate_transaction_energy_bounds,
        )

        qs = Transaction.objects.all()

        start_dt = None
        end_dt = None
        if start_date:
            start_dt = datetime.combine(start_date, time.min, tzinfo=pytimezone.utc)
            qs = qs.filter(start_time__gte=start_dt)
        if end_date:
            end_dt = datetime.combine(
                end_date + timedelta(days=1), time.min, tzinfo=pytimezone.utc
            )
            qs = qs.filter(start_time__lt=end_dt)

        selected_base_ids = None
        if chargers:
            selected_base_ids = {
                charger.charger_id for charger in chargers if charger.charger_id
            }
            if selected_base_ids:
                qs = qs.filter(charger__charger_id__in=selected_base_ids)

        qs = qs.select_related("account", "charger")
        qs = annotate_transaction_energy_bounds(
            qs,
            start_field="report_meter_energy_start",
            end_field="report_meter_energy_end",
        )
        transactions = list(qs.order_by("start_time", "pk"))

        rfid_values = {tx.rfid for tx in transactions if tx.rfid}
        tag_map: dict[str, RFID] = {}
        if rfid_values:
            tag_map = {
                tag.rfid: tag
                for tag in RFID.objects.filter(rfid__in=rfid_values).prefetch_related(
                    "energy_accounts"
                )
            }

        charger_ids = {
            tx.charger.charger_id
            for tx in transactions
            if getattr(tx, "charger", None) and tx.charger.charger_id
        }
        aggregator_map: dict[str, Charger] = {}
        if charger_ids:
            aggregator_map = {
                charger.charger_id: charger
                for charger in Charger.objects.filter(
                    charger_id__in=charger_ids, connector_id__isnull=True
                )
            }

        groups: dict[str, dict[str, Any]] = {}
        for tx in transactions:
            charger = getattr(tx, "charger", None)
            if charger is None:
                continue
            base_id = charger.charger_id
            if selected_base_ids is not None and base_id not in selected_base_ids:
                continue
            aggregator = aggregator_map.get(base_id) or charger
            entry = groups.setdefault(
                base_id,
                {"charger": aggregator, "transactions": []},
            )
            entry["transactions"].append(tx)

        evcs_entries: list[dict[str, Any]] = []
        total_all_time = 0.0
        total_period = 0.0

        def _sort_key(tx):
            anchor = getattr(tx, "start_time", None)
            if anchor is None:
                anchor = datetime.min.replace(tzinfo=pytimezone.utc)
            return (anchor, tx.pk or 0)

        for base_id, info in sorted(groups.items(), key=lambda item: item[0]):
            aggregator = info["charger"]
            txs = sorted(info["transactions"], key=_sort_key)
            total_kw_all = float(getattr(aggregator, "total_kw", 0.0) or 0.0)
            total_kw_period = 0.0
            if hasattr(aggregator, "total_kw_for_range"):
                total_kw_period = float(
                    aggregator.total_kw_for_range(start=start_dt, end=end_dt) or 0.0
                )
            total_all_time += total_kw_all
            total_period += total_kw_period

            session_rows: list[dict[str, Any]] = []
            for tx in txs:
                session_kw = float(getattr(tx, "kw", 0.0) or 0.0)
                if session_kw <= 0:
                    continue

                start_kwh, end_kwh = ClientReport._resolve_meter_bounds(tx)

                connector_number = (
                    tx.connector_id
                    if getattr(tx, "connector_id", None) is not None
                    else getattr(getattr(tx, "charger", None), "connector_id", None)
                )
                connector_letter = (
                    Charger.connector_letter_from_value(connector_number)
                    if connector_number not in {None, ""}
                    else None
                )
                connector_order = (
                    connector_number
                    if isinstance(connector_number, int)
                    else None
                )

                rfid_value = (tx.rfid or "").strip()
                tag = tag_map.get(rfid_value)
                label = None
                account_name = (
                    tx.account.name
                    if tx.account and getattr(tx.account, "name", None)
                    else None
                )
                if tag:
                    label = tag.custom_label or str(tag.label_id)
                    if not account_name:
                        account = next(iter(tag.energy_accounts.all()), None)
                        if account and getattr(account, "name", None):
                            account_name = account.name
                elif rfid_value:
                    label = rfid_value

                session_rows.append(
                    {
                        "connector": connector_number,
                        "connector_label": connector_letter,
                        "connector_order": connector_order,
                        "rfid_label": label,
                        "account_name": account_name,
                        "start_kwh": start_kwh,
                        "end_kwh": end_kwh,
                        "session_kwh": session_kw,
                        "start": tx.start_time.isoformat()
                        if getattr(tx, "start_time", None)
                        else None,
                        "end": tx.stop_time.isoformat()
                        if getattr(tx, "stop_time", None)
                        else None,
                    }
                )

            evcs_entries.append(
                {
                    "charger_id": aggregator.pk,
                    "serial_number": aggregator.charger_id,
                    "display_name": aggregator.display_name
                    or aggregator.name
                    or aggregator.charger_id,
                    "total_kw": total_kw_all,
                    "total_kw_period": total_kw_period,
                    "transactions": session_rows,
                }
            )

        filters: dict[str, Any] = {}
        if selected_base_ids:
            filters["chargers"] = sorted(selected_base_ids)

        return {
            "schema": "evcs-session/v1",
            "evcs": evcs_entries,
            "totals": {
                "total_kw": total_all_time,
                "total_kw_period": total_period,
            },
            "filters": filters,
        }

    @staticmethod
    def _resolve_meter_bounds(tx) -> tuple[float | None, float | None]:
        def _convert(value):
            if value in {None, ""}:
                return None
            try:
                return float(value) / 1000.0
            except (TypeError, ValueError):
                return None

        start_value = _convert(getattr(tx, "meter_start", None))
        end_value = _convert(getattr(tx, "meter_stop", None))

        def _coerce_energy(value):
            if value in {None, ""}:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        if start_value is None:
            annotated_start = getattr(tx, "report_meter_energy_start", None)
            start_value = _coerce_energy(annotated_start)

        if end_value is None:
            annotated_end = getattr(tx, "report_meter_energy_end", None)
            end_value = _coerce_energy(annotated_end)

        if start_value is None or end_value is None:
            readings_manager = getattr(tx, "meter_values", None)
            if readings_manager is not None:
                qs = readings_manager.filter(energy__isnull=False).order_by("timestamp")
                if start_value is None:
                    first_energy = qs.values_list("energy", flat=True).first()
                    start_value = _coerce_energy(first_energy)
                if end_value is None:
                    last_energy = qs.order_by("-timestamp").values_list(
                        "energy", flat=True
                    ).first()
                    end_value = _coerce_energy(last_energy)

        return start_value, end_value

    @staticmethod
    def _format_session_datetime(value):
        if not value:
            return None
        localized = timezone.localtime(value)
        date_part = formats.date_format(
            localized, format="MONTH_DAY_FORMAT", use_l10n=True
        )
        time_part = formats.time_format(
            localized, format="TIME_FORMAT", use_l10n=True
        )
        return gettext("%(date)s, %(time)s") % {
            "date": date_part,
            "time": time_part,
        }

    @staticmethod
    def _calculate_duration_minutes(start, end):
        if not start or not end:
            return None
        total_seconds = (end - start).total_seconds()
        if total_seconds < 0:
            return None
        return int(round(total_seconds / 60.0))

    @staticmethod
    def _normalize_dataset_for_display(dataset: dict[str, Any]):
        schema = dataset.get("schema")
        if schema == "evcs-session/v1":
            from datetime import datetime

            evcs_entries: list[dict[str, Any]] = []
            for entry in dataset.get("evcs", []):
                normalized_rows: list[dict[str, Any]] = []
                for row in entry.get("transactions", []):
                    start_val = row.get("start")
                    end_val = row.get("end")

                    start_dt = None
                    if start_val:
                        start_dt = parse_datetime(start_val)
                        if start_dt and timezone.is_naive(start_dt):
                            start_dt = timezone.make_aware(start_dt, timezone.utc)

                    end_dt = None
                    if end_val:
                        end_dt = parse_datetime(end_val)
                        if end_dt and timezone.is_naive(end_dt):
                            end_dt = timezone.make_aware(end_dt, timezone.utc)

                normalized_rows.append(
                    {
                        "connector": row.get("connector"),
                        "connector_label": row.get("connector_label"),
                        "connector_order": row.get("connector_order"),
                        "rfid_label": row.get("rfid_label"),
                        "account_name": row.get("account_name"),
                        "start_kwh": row.get("start_kwh"),
                        "end_kwh": row.get("end_kwh"),
                        "session_kwh": row.get("session_kwh"),
                            "start": start_dt,
                            "end": end_dt,
                            "start_display": ClientReport._format_session_datetime(
                                start_dt
                            ),
                            "end_display": ClientReport._format_session_datetime(
                                end_dt
                            ),
                            "duration_minutes": ClientReport._calculate_duration_minutes(
                                start_dt, end_dt
                            ),
                        }
                    )

                def _connector_sort_value(item):
                    order_value = item.get("connector_order")
                    if isinstance(order_value, int):
                        return order_value
                    connector_value = item.get("connector")
                    if isinstance(connector_value, int):
                        return connector_value
                    try:
                        return int(connector_value)
                    except (TypeError, ValueError):
                        return 0

                normalized_rows.sort(
                    key=lambda item: (
                        item["start"]
                        if item["start"] is not None
                        else datetime.min.replace(tzinfo=timezone.utc),
                        _connector_sort_value(item),
                    )
                )

                evcs_entries.append(
                    {
                        "display_name": entry.get("display_name")
                        or entry.get("serial_number")
                        or "Charge Point",
                        "serial_number": entry.get("serial_number"),
                        "total_kw": entry.get("total_kw", 0.0),
                        "total_kw_period": entry.get("total_kw_period", 0.0),
                        "transactions": normalized_rows,
                    }
                )

            totals = dataset.get("totals", {})
            return {
                "schema": schema,
                "evcs": evcs_entries,
                "totals": {
                    "total_kw": totals.get("total_kw", 0.0),
                    "total_kw_period": totals.get("total_kw_period", 0.0),
                },
                "filters": dataset.get("filters", {}),
            }

        if schema == "session-list/v1":
            parsed: list[dict[str, Any]] = []
            for row in dataset.get("rows", []):
                item = dict(row)
                start_val = row.get("start")
                end_val = row.get("end")

                if start_val:
                    start_dt = parse_datetime(start_val)
                    if start_dt and timezone.is_naive(start_dt):
                        start_dt = timezone.make_aware(start_dt, timezone.utc)
                    item["start"] = start_dt
                else:
                    start_dt = None
                    item["start"] = None

                if end_val:
                    end_dt = parse_datetime(end_val)
                    if end_dt and timezone.is_naive(end_dt):
                        end_dt = timezone.make_aware(end_dt, timezone.utc)
                    item["end"] = end_dt
                else:
                    end_dt = None
                    item["end"] = None

                item["start_display"] = ClientReport._format_session_datetime(start_dt)
                item["end_display"] = ClientReport._format_session_datetime(end_dt)
                item["duration_minutes"] = ClientReport._calculate_duration_minutes(
                    start_dt, end_dt
                )

                parsed.append(item)

            return {"schema": schema, "rows": parsed}

        return {
            "schema": schema,
            "rows": dataset.get("rows", []),
            "filters": dataset.get("filters", {}),
        }

    @staticmethod
    def build_evcs_summary_rows(dataset: dict[str, Any] | None):
        """Flatten EVCS session data for summarized presentations."""

        if not dataset or dataset.get("schema") != "evcs-session/v1":
            return []

        summary_rows: list[dict[str, Any]] = []
        for entry in dataset.get("evcs", []):
            if not isinstance(entry, dict):
                continue

            display_name = (
                entry.get("display_name")
                or entry.get("serial_number")
                or gettext("Charge Point")
            )
            serial_number = entry.get("serial_number")
            transactions = entry.get("transactions") or []
            if not isinstance(transactions, list):
                continue

            for row in transactions:
                if not isinstance(row, dict):
                    continue
                summary_rows.append(
                    {
                        "display_name": display_name,
                        "serial_number": serial_number,
                        "transaction": row,
                    }
                )

        return summary_rows

    @property
    def rows_for_display(self):
        data = self.data or {}
        return ClientReport._normalize_dataset_for_display(data)

    @staticmethod
    def _relative_to_base(path: Path, base_dir: Path) -> str:
        try:
            return str(path.relative_to(base_dir))
        except ValueError:
            return str(path)

    @classmethod
    def _load_pdf_template(cls, language_code: str | None) -> dict[str, str]:
        from django.template import TemplateDoesNotExist
        from django.template.loader import render_to_string

        candidates: list[str] = []
        normalized = cls.normalize_language(language_code)
        if normalized:
            candidates.append(normalized)

        default_code = default_report_language()
        if default_code and default_code not in candidates:
            candidates.append(default_code)

        if "en" not in candidates:
            candidates.append("en")

        for code in dict.fromkeys(candidates):
            template_name = f"core/reports/client_report_pdf/{code}.json"
            try:
                rendered = render_to_string(template_name)
            except TemplateDoesNotExist:
                continue
            if not rendered:
                continue
            try:
                data = json.loads(rendered)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid client report PDF template %s", template_name, exc_info=True
                )
                continue
            if isinstance(data, dict):
                return data

        return {}

    @staticmethod
    def resolve_reply_to_for_owner(owner) -> list[str]:
        if not owner:
            return []
        try:
            inbox_model = apps.get_model("teams", "EmailInbox")
        except LookupError:
            inbox_model = None
        try:
            inbox = owner.get_profile(inbox_model) if inbox_model else None
        except Exception:  # pragma: no cover - defensive catch
            inbox = None
        if inbox and getattr(inbox, "username", ""):
            address = inbox.username.strip()
            if address:
                return [address]
        return []

    @staticmethod
    def resolve_outbox_for_owner(owner):
        from nodes.models import Node

        try:
            outbox_model = apps.get_model("teams", "EmailOutbox")
        except LookupError:
            outbox_model = None

        if owner:
            try:
                outbox = owner.get_profile(outbox_model) if outbox_model else None
            except Exception:  # pragma: no cover - defensive catch
                outbox = None
            if outbox:
                return outbox

        node = Node.get_local()
        if node:
            return getattr(node, "email_outbox", None)
        return None

    def render_pdf(self, target: Path):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        dataset = self.rows_for_display
        schema = dataset.get("schema")

        language_code = self.normalize_language(self.language)
        with override(language_code):
            styles = getSampleStyleSheet()
            title_style = styles["Title"]
            subtitle_style = styles["Heading2"]
            normal_style = styles["BodyText"]
            emphasis_style = styles["Heading3"]

            document = SimpleDocTemplate(
                str(target_path),
                pagesize=landscape(letter),
                leftMargin=0.5 * inch,
                rightMargin=0.5 * inch,
                topMargin=0.6 * inch,
                bottomMargin=0.5 * inch,
            )

            story: list = []
            labels = self._load_pdf_template(language_code)

            def label(key: str, default: str) -> str:
                value = labels.get(key) if isinstance(labels, dict) else None
                if isinstance(value, str) and value.strip():
                    return value
                return gettext(default)

            report_title = self.normalize_title(self.title) or label(
                "title", "Consumer Report"
            )
            story.append(Paragraph(report_title, title_style))

            start_display = formats.date_format(
                self.start_date, format="DATE_FORMAT", use_l10n=True
            )
            end_display = formats.date_format(
                self.end_date, format="DATE_FORMAT", use_l10n=True
            )
            default_period_text = gettext("Period: %(start)s to %(end)s") % {
                "start": start_display,
                "end": end_display,
            }
            period_template = labels.get("period") if isinstance(labels, dict) else None
            if isinstance(period_template, str):
                try:
                    period_text = period_template.format(
                        start=start_display, end=end_display
                    )
                except (KeyError, IndexError, ValueError):
                    logger.warning(
                        "Invalid period template for client report PDF: %s",
                        period_template,
                    )
                    period_text = default_period_text
            else:
                period_text = default_period_text
            story.append(Paragraph(period_text, emphasis_style))
            story.append(Spacer(1, 0.25 * inch))

            total_kw_all_time_label = label("total_kw_all_time", "Total kW (all time)")
            total_kw_period_label = label("total_kw_period", "Total kW (period)")
            connector_label = label("connector", "Connector")
            account_label = label("account", "Account")
            session_kwh_label = label("session_kwh", "Session kW")
            session_start_label = label("session_start", "Session start")
            session_end_label = label("session_end", "Session end")
            time_label = label("time", "Time")
            rfid_label = label("rfid_label", "RFID label")
            no_sessions_period = label(
                "no_sessions_period",
                "No charging sessions recorded for the selected period.",
            )
            no_sessions_point = label(
                "no_sessions_point",
                "No charging sessions recorded for this charge point.",
            )
            no_structured_data = label(
                "no_structured_data",
                "No structured data is available for this report.",
            )
            report_totals_label = label("report_totals", "Report totals")
            total_kw_period_line = label(
                "total_kw_period_line", "Total kW during period"
            )
            charge_point_label = label("charge_point", "Charge Point")
            serial_template = (
                labels.get("charge_point_serial")
                if isinstance(labels, dict)
                else None
            )

            def format_datetime(value):
                if not value:
                    return "—"
                return ClientReport._format_session_datetime(value) or "—"

            def format_decimal(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=2, use_l10n=True)

            def format_duration(value):
                if value is None:
                    return "—"
                return formats.number_format(value, decimal_pos=0, use_l10n=True)

            if schema == "evcs-session/v1":
                evcs_entries = dataset.get("evcs", [])
                if not evcs_entries:
                    story.append(Paragraph(no_sessions_period, normal_style))
                for index, evcs in enumerate(evcs_entries):
                    if index:
                        story.append(Spacer(1, 0.2 * inch))

                    display_name = evcs.get("display_name") or charge_point_label
                    serial_number = evcs.get("serial_number")
                    if serial_number:
                        if isinstance(serial_template, str):
                            try:
                                header_text = serial_template.format(
                                    name=display_name, serial=serial_number
                                )
                            except (KeyError, IndexError, ValueError):
                                header_text = serial_template
                        else:
                            header_text = gettext("%(name)s (Serial: %(serial)s)") % {
                                "name": display_name,
                                "serial": serial_number,
                            }
                    else:
                        header_text = display_name
                    story.append(Paragraph(header_text, subtitle_style))

                    metrics_text = (
                        f"{total_kw_all_time_label}: "
                        f"{format_decimal(evcs.get('total_kw', 0.0))} | "
                        f"{total_kw_period_label}: "
                        f"{format_decimal(evcs.get('total_kw_period', 0.0))}"
                    )
                    story.append(Paragraph(metrics_text, normal_style))
                    story.append(Spacer(1, 0.1 * inch))

                    transactions = evcs.get("transactions", [])
                    if transactions:
                        table_data = [
                            [
                                session_kwh_label,
                                session_start_label,
                                session_end_label,
                                time_label,
                                connector_label,
                                rfid_label,
                                account_label,
                            ]
                        ]

                        for row in transactions:
                            start_dt = row.get("start")
                            end_dt = row.get("end")
                            duration_value = row.get("duration_minutes")
                            table_data.append(
                                [
                                    format_decimal(row.get("session_kwh")),
                                    format_datetime(start_dt),
                                    format_datetime(end_dt),
                                    format_duration(duration_value),
                                    (
                                        row.get("connector_label")
                                        or row.get("connector")
                                    )
                                    if row.get("connector") is not None
                                    or row.get("connector_label")
                                    else "—",
                                    row.get("rfid_label") or "—",
                                    row.get("account_name") or "—",
                                ]
                            )

                        column_count = len(table_data[0])
                        col_width = document.width / column_count if column_count else None
                        table = Table(
                            table_data,
                            repeatRows=1,
                            colWidths=[col_width] * column_count if col_width else None,
                            hAlign="LEFT",
                        )
                        table.setStyle(
                            TableStyle(
                                [
                                    (
                                        "BACKGROUND",
                                        (0, 0),
                                        (-1, 0),
                                        colors.HexColor("#0f172a"),
                                    ),
                                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                    ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                    ("FONTSIZE", (0, 0), (-1, 0), 9),
                                    (
                                        "ROWBACKGROUNDS",
                                        (0, 1),
                                        (-1, -1),
                                        [colors.whitesmoke, colors.HexColor("#eef2ff")],
                                    ),
                                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                    ("VALIGN", (0, 1), (-1, -1), "MIDDLE"),
                                ]
                            )
                        )
                        story.append(table)
                    else:
                        story.append(Paragraph(no_sessions_point, normal_style))
            else:
                story.append(Paragraph(no_structured_data, normal_style))

            totals = dataset.get("totals") or {}
            story.append(Spacer(1, 0.3 * inch))
            story.append(Paragraph(report_totals_label, emphasis_style))
            story.append(
                Paragraph(
                    f"{total_kw_all_time_label}: "
                    f"{format_decimal(totals.get('total_kw', 0.0))}",
                    emphasis_style,
                )
            )
            story.append(
                Paragraph(
                    f"{total_kw_period_line}: "
                    f"{format_decimal(totals.get('total_kw_period', 0.0))}",
                    emphasis_style,
                )
            )

            document.build(story)

    def ensure_pdf(self) -> Path:
        base_dir = Path(settings.BASE_DIR)
        export = dict((self.data or {}).get("export") or {})
        pdf_relative = export.get("pdf_path")
        if pdf_relative:
            candidate = base_dir / pdf_relative
            if candidate.exists():
                return candidate

        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"
        pdf_path = report_dir / f"{identifier}.pdf"
        self.render_pdf(pdf_path)

        export["pdf_path"] = ClientReport._relative_to_base(pdf_path, base_dir)
        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return pdf_path


class Product(Entity):
    """A product that users can subscribe to."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    renewal_period = models.PositiveIntegerField(help_text="Renewal period in days")
    odoo_product = models.JSONField(
        null=True,
        blank=True,
        help_text="Selected product from Odoo (id and name)",
    )

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name


    class Meta:
        verbose_name = _("Product")
        verbose_name_plural = _("Products")


class AdminHistory(Entity):
    """Record of recently visited admin changelists for a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="admin_history"
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    url = models.TextField()
    visited_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visited_at"]
        unique_together = ("user", "url")
        verbose_name = "Admin History"
        verbose_name_plural = "Admin Histories"

    @property
    def admin_label(self) -> str:  # pragma: no cover - simple representation
        model = self.content_type.model_class()
        return model._meta.verbose_name_plural if model else self.content_type.name


class ReleaseManagerManager(EntityManager):
    def get_by_natural_key(self, owner, package=None):
        owner = owner or ""
        if owner.startswith("group:"):
            group_name = owner.split(":", 1)[1]
            return self.get(group__name=group_name)
        return self.get(user__username=owner)


class PackageManager(EntityManager):
    def get_by_natural_key(self, name):
        return self.get(name=name)


class PackageReleaseManager(EntityManager):
    def get_by_natural_key(self, package, version):
        return self.get(package__name=package, version=version)


class ReleaseManager(Profile):
    """Store credentials for publishing packages."""

    objects = ReleaseManagerManager()

    def natural_key(self):
        owner = self.owner_display()
        if self.group_id and owner:
            owner = f"group:{owner}"

        pkg_name = ""
        if self.pk:
            pkg = self.package_set.first()
            pkg_name = pkg.name if pkg else ""

        return (owner or "", pkg_name)

    profile_fields = (
        "pypi_username",
        "pypi_token",
        "github_token",
        "git_username",
        "git_password",
        "pypi_password",
        "pypi_url",
        "secondary_pypi_url",
    )
    pypi_username = SigilShortAutoField("PyPI username", max_length=100, blank=True)
    pypi_token = SigilShortAutoField("PyPI token", max_length=200, blank=True)
    github_token = SigilShortAutoField(
        max_length=200,
        blank=True,
        help_text=(
            "Personal access token for GitHub operations. "
            "Used before the GITHUB_TOKEN environment variable."
        ),
    )
    git_username = SigilShortAutoField(
        "Git username",
        max_length=100,
        blank=True,
        help_text="Username used for Git pushes (for example, your GitHub username).",
    )
    git_password = SigilShortAutoField(
        "Git password/token",
        max_length=200,
        blank=True,
        help_text=(
            "Password or personal access token for HTTPS Git pushes. "
            "Leave blank to use the GitHub token instead."
        ),
    )
    pypi_password = SigilShortAutoField("PyPI password", max_length=200, blank=True)
    pypi_url = SigilShortAutoField(
        "PyPI URL",
        max_length=200,
        blank=True,
        help_text=(
            "Link to the PyPI user profile (for example, https://pypi.org/user/username/). "
            "Use the account's user page, not a project-specific URL. "
            "This value is informational and not used for uploads."
        ),
    )
    secondary_pypi_url = SigilShortAutoField(
        "Secondary PyPI URL",
        max_length=200,
        blank=True,
        help_text=(
            "Optional secondary repository upload endpoint."
            " Leave blank to disable mirrored uploads."
        ),
    )

    class Meta:
        verbose_name = "Release Manager"
        verbose_name_plural = "Release Managers"
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="releasemanager_requires_owner",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    @property
    def name(self) -> str:  # pragma: no cover - simple proxy
        owner = self.owner_display()
        return owner or ""

    def to_credentials(self) -> Credentials | None:
        """Return credentials for this release manager."""
        if self.pypi_token:
            return Credentials(token=self.pypi_token)
        if self.pypi_username and self.pypi_password:
            return Credentials(username=self.pypi_username, password=self.pypi_password)
        return None

    def to_git_credentials(self) -> GitCredentials | None:
        """Return Git credentials for pushing tags."""

        username = (self.git_username or "").strip()
        password_source = self.git_password or self.github_token or ""
        password = password_source.strip()

        if password and not username and password_source == self.github_token:
            # GitHub personal access tokens require a username when used for
            # HTTPS pushes. Default to the recommended ``x-access-token`` so
            # release managers only need to provide their token.
            username = "x-access-token"

        if username and password:
            return GitCredentials(username=username, password=password)
        return None


class Package(Entity):
    """Package details shared across releases."""

    objects = PackageManager()

    def natural_key(self):
        return (self.name,)

    name = models.CharField(max_length=100, default=DEFAULT_PACKAGE.name, unique=True)
    description = models.CharField(max_length=255, default=DEFAULT_PACKAGE.description)
    author = models.CharField(max_length=100, default=DEFAULT_PACKAGE.author)
    email = models.EmailField(default=DEFAULT_PACKAGE.email)
    python_requires = models.CharField(
        max_length=20, default=DEFAULT_PACKAGE.python_requires
    )
    license = models.CharField(max_length=100, default=DEFAULT_PACKAGE.license)
    repository_url = models.URLField(default=DEFAULT_PACKAGE.repository_url)
    homepage_url = models.URLField(default=DEFAULT_PACKAGE.homepage_url)
    version_path = models.CharField(max_length=255, blank=True, default="")
    dependencies_path = models.CharField(max_length=255, blank=True, default="")
    test_command = models.TextField(blank=True, default="")
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    is_active = models.BooleanField(
        default=False,
        help_text="Designates the active package for version comparisons",
    )

    class Meta:
        verbose_name = "Package"
        verbose_name_plural = "Packages"
        constraints = [
            models.UniqueConstraint(
                fields=("is_active",),
                condition=models.Q(is_active=True),
                name="unique_active_package",
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name

    def save(self, *args, **kwargs):
        if self.is_active:
            type(self).objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` instance from package data."""
        return ReleasePackage(
            name=self.name,
            description=self.description,
            author=self.author,
            email=self.email,
            python_requires=self.python_requires,
            license=self.license,
            repository_url=self.repository_url,
            homepage_url=self.homepage_url,
            version_path=self.version_path or None,
            dependencies_path=self.dependencies_path or None,
            test_command=self.test_command or None,
        )


class PackageRelease(Entity):
    """Store metadata for a specific package version."""

    _PATCH_BITS = 12
    _MINOR_BITS = 12
    _PATCH_MASK = (1 << _PATCH_BITS) - 1
    _MINOR_MASK = (1 << _MINOR_BITS) - 1
    _MINOR_SHIFT = _PATCH_BITS
    _MAJOR_SHIFT = _PATCH_BITS + _MINOR_BITS

    objects = PackageReleaseManager()

    def natural_key(self):
        return (self.package.name, self.version)

    class Severity(models.TextChoices):
        NORMAL = "normal", _("Normal")
        LOW = "low", _("Low")
        CRITICAL = "critical", _("Critical")

    package = models.ForeignKey(
        Package, on_delete=models.CASCADE, related_name="releases"
    )
    release_manager = models.ForeignKey(
        ReleaseManager, on_delete=models.SET_NULL, null=True, blank=True
    )
    version = models.CharField(max_length=20, default="0.0.0")
    revision = models.CharField(
        max_length=40, blank=True, default=revision_utils.get_revision, editable=False
    )
    severity = models.CharField(
        max_length=16,
        choices=Severity.choices,
        default=Severity.NORMAL,
        help_text=_("Controls the expected urgency for auto-upgrades."),
    )
    changelog = models.TextField(blank=True, default="")
    pypi_url = models.URLField("PyPI URL", blank=True, editable=False)
    github_url = models.URLField("GitHub URL", blank=True, editable=False)
    release_on = models.DateTimeField(blank=True, null=True, editable=False)

    class Meta:
        verbose_name = "Package Release"
        verbose_name_plural = "Package Releases"
        get_latest_by = "version"
        constraints = [
            models.UniqueConstraint(
                fields=("package", "version"), name="unique_package_version"
            )
        ]

    @classmethod
    def dump_fixture(cls) -> None:
        base = Path("core/fixtures")
        base.mkdir(parents=True, exist_ok=True)
        existing = {path.name: path for path in base.glob("releases__*.json")}
        expected: set[str] = set()
        for release in cls.objects.all():
            name = f"releases__packagerelease_{release.version.replace('.', '_')}.json"
            path = base / name
            data = serializers.serialize(
                "json",
                [release],
                use_natural_foreign_keys=True,
                use_natural_primary_keys=True,
            )
            data = json.dumps(json.loads(data), indent=2) + "\n"
            expected.add(name)
            try:
                current = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                current = None
            if current != data:
                path.write_text(data, encoding="utf-8")
        for old_name, old_path in existing.items():
            if old_name not in expected and old_path.exists():
                old_path.unlink()

    def delete(self, using=None, keep_parents=False):
        user_data.delete_user_fixture(self)
        super().delete(using=using, keep_parents=keep_parents)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.package.name} {self.version}"

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` built from the package."""
        return self.package.to_package()

    def to_credentials(
        self, user: models.Model | None = None
    ) -> Credentials | None:
        """Return :class:`Credentials` from available release managers."""

        manager_candidates: list[ReleaseManager] = []

        for candidate in (self.release_manager, self.package.release_manager):
            if candidate and candidate not in manager_candidates:
                manager_candidates.append(candidate)

        if user is not None and getattr(user, "is_authenticated", False):
            try:
                user_manager = ReleaseManager.objects.get(user=user)
            except ReleaseManager.DoesNotExist:
                user_manager = None
            else:
                if user_manager not in manager_candidates:
                    manager_candidates.append(user_manager)

        for manager in manager_candidates:
            creds = manager.to_credentials()
            if creds and creds.has_auth():
                return creds

        token = (os.environ.get("PYPI_API_TOKEN") or "").strip()
        username = (os.environ.get("PYPI_USERNAME") or "").strip()
        password = (os.environ.get("PYPI_PASSWORD") or "").strip()

        if token:
            return Credentials(token=token)
        if username and password:
            return Credentials(username=username, password=password)
        return None

    def get_github_token(self) -> str | None:
        """Return GitHub token from the associated release manager or environment."""
        manager = self.release_manager or self.package.release_manager
        if manager and manager.github_token:
            return manager.github_token
        return os.environ.get("GITHUB_TOKEN")

    def build_publish_targets(
        self, user: models.Model | None = None
    ) -> list[RepositoryTarget]:
        """Return repository targets for publishing this release."""

        manager = self.release_manager or self.package.release_manager
        targets: list[RepositoryTarget] = []

        env_primary = os.environ.get("PYPI_REPOSITORY_URL", "")
        primary_url = env_primary.strip()

        primary_creds = self.to_credentials(user=user)
        targets.append(
            RepositoryTarget(
                name="PyPI",
                repository_url=primary_url or None,
                credentials=primary_creds,
                verify_availability=True,
            )
        )

        secondary_url = ""
        if manager and getattr(manager, "secondary_pypi_url", ""):
            secondary_url = manager.secondary_pypi_url.strip()
        if not secondary_url:
            env_secondary = os.environ.get("PYPI_SECONDARY_URL", "")
            secondary_url = env_secondary.strip()
        if not secondary_url:
            return targets

        def _clone_credentials(creds: Credentials | None) -> Credentials | None:
            if creds is None or not creds.has_auth():
                return None
            return Credentials(
                token=creds.token,
                username=creds.username,
                password=creds.password,
            )

        github_token = self.get_github_token()
        github_username = None
        if manager and manager.pypi_username:
            github_username = manager.pypi_username.strip() or None
        env_secondary_username = os.environ.get("PYPI_SECONDARY_USERNAME")
        env_secondary_password = os.environ.get("PYPI_SECONDARY_PASSWORD")
        if not github_username:
            github_username = (
                os.environ.get("GITHUB_USERNAME")
                or os.environ.get("GITHUB_ACTOR")
                or (env_secondary_username.strip() if env_secondary_username else None)
            )

        password_candidate = github_token or (
            env_secondary_password.strip() if env_secondary_password else None
        )

        secondary_creds: Credentials | None = None
        if github_username and password_candidate:
            secondary_creds = Credentials(
                username=github_username,
                password=password_candidate,
            )
        else:
            secondary_creds = _clone_credentials(primary_creds)

        if secondary_creds and secondary_creds.has_auth():
            name = "GitHub Packages" if github_token else "Secondary repository"
            targets.append(
                RepositoryTarget(
                    name=name,
                    repository_url=secondary_url,
                    credentials=secondary_creds,
                )
            )

        return targets

    def github_package_url(self) -> str | None:
        """Return the GitHub Packages URL for this release if determinable."""

        repo_url = self.package.repository_url
        if not repo_url:
            return None
        parsed = urlparse(repo_url)
        if "github.com" not in parsed.netloc.lower():
            return None
        path = parsed.path.strip("/")
        if not path:
            return None
        if path.endswith(".git"):
            path = path[: -len(".git")]
        return (
            f"https://github.com/{path}/pkgs/pypi/{self.package.name}"
            f"/versions?version={quote_plus(self.version)}"
        )

    @property
    def migration_number(self) -> int:
        """Return the migration number derived from the version bits."""
        from packaging.version import Version

        v = Version(self.version)
        return (
            (v.major << self._MAJOR_SHIFT)
            | (v.minor << self._MINOR_SHIFT)
            | v.micro
        )

    @staticmethod
    def version_from_migration(number: int) -> str:
        """Return version string encoded by ``number``."""
        major = number >> PackageRelease._MAJOR_SHIFT
        minor = (number >> PackageRelease._MINOR_SHIFT) & PackageRelease._MINOR_MASK
        patch = number & PackageRelease._PATCH_MASK
        return f"{major}.{minor}.{patch}"

    @property
    def is_published(self) -> bool:
        """Return ``True`` if this release has been published."""
        return bool(self.pypi_url)

    @property
    def is_current(self) -> bool:
        """Return ``True`` when this release's version matches the VERSION file
        and its package is active."""
        version_path = Path("VERSION")
        if not version_path.exists():
            return False
        current_version = version_path.read_text().strip()
        return current_version == self.version and self.package.is_active

    @classmethod
    def latest(cls):
        """Return the latest release by version, preferring active packages."""
        from packaging.version import Version

        releases = list(cls.objects.filter(package__is_active=True))
        if not releases:
            releases = list(cls.objects.all())
        if not releases:
            return None
        return max(releases, key=lambda r: Version(r.version))

    @classmethod
    def matches_revision(cls, version: str, revision: str) -> bool:
        """Return ``True`` when *revision* matches the stored release revision.

        When the release metadata cannot be retrieved (for example during
        database initialization), the method optimistically returns ``True`` so
        callers continue operating without raising secondary errors.
        """

        version = (version or "").strip()
        if version.endswith("+"):
            version = version.rstrip("+")
        revision = (revision or "").strip()
        if not version or not revision:
            return True

        try:
            queryset = cls.objects.filter(version=version)
            release_revision = (
                queryset.filter(package__is_active=True)
                .values_list("revision", flat=True)
                .first()
            )
            if release_revision is None:
                release_revision = queryset.values_list("revision", flat=True).first()
        except DatabaseError:  # pragma: no cover - depends on DB availability
            logger.debug(
                "PackageRelease.matches_revision skipped: database unavailable",
                exc_info=True,
            )
            return True

        if not release_revision:
            return True
        return release_revision.strip() == revision

    def build(self, **kwargs) -> None:
        """Wrapper around :func:`core.release.build` for convenience."""
        from . import release as release_utils
        from utils import revision as revision_utils

        release_utils.build(
            package=self.to_package(),
            version=self.version,
            creds=self.to_credentials(),
            **kwargs,
        )
        self.revision = revision_utils.get_revision()
        self.save(update_fields=["revision"])
        PackageRelease.dump_fixture()
        if kwargs.get("git"):
            from glob import glob

            paths = sorted(glob("core/fixtures/releases__*.json"))
            diff = subprocess.run(
                ["git", "status", "--porcelain", *paths],
                capture_output=True,
                text=True,
            )
            if diff.stdout.strip():
                release_utils._run(["git", "add", *paths])
                release_utils._run(
                    [
                        "git",
                        "commit",
                        "-m",
                        f"chore: update release fixture for v{self.version}",
                    ]
                )
                release_utils._run(["git", "push"])

    @property
    def revision_short(self) -> str:
        return self.revision[-6:] if self.revision else ""


# Ensure each RFID can only be linked to one customer account
@receiver(m2m_changed, sender=CustomerAccount.rfids.through)
def _rfid_unique_customer_account(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Prevent associating an RFID with more than one customer account."""
    if action == "pre_add":
        if reverse:  # adding customer accounts to an RFID
            if instance.energy_accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one customer account."
                )
        else:  # adding RFIDs to a customer account
            conflict = model.objects.filter(
                pk__in=pk_set, energy_accounts__isnull=False
            ).exclude(energy_accounts=instance)
            if conflict.exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one customer account."
                )

def validate_relative_url(value: str) -> None:
    if not value:
        return
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or not value.startswith("/"):
        raise ValidationError("URL must be relative")


class TodoManager(EntityManager):
    def get_by_natural_key(self, request: str):
        return self.get(request=request)

class Todo(Entity):
    """Tasks requested for the Release Manager."""

    request = models.CharField(max_length=255)
    version = models.CharField(max_length=20, blank=True, default="")
    created_on = models.DateTimeField(auto_now_add=True)
    url = models.CharField(
        max_length=200, blank=True, default="", validators=[validate_relative_url]
    )
    request_details = models.TextField(blank=True, default="")
    generated_for_version = models.CharField(max_length=20, blank=True, default="")
    generated_for_revision = models.CharField(max_length=40, blank=True, default="")
    done_on = models.DateTimeField(null=True, blank=True)
    done_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="completed_todos",
        help_text="Node where this TODO was completed.",
    )
    done_version = models.CharField(max_length=20, blank=True, default="")
    done_revision = models.CharField(max_length=40, blank=True, default="")
    done_username = models.CharField(max_length=150, blank=True, default="")
    on_done_condition = ConditionTextField(blank=True, default="")
    stale_on = models.DateTimeField(null=True, blank=True)
    origin_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="originated_todos",
        help_text="Node where this TODO was generated.",
    )
    original_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="originated_todos",
        help_text="User responsible for creating this TODO.",
    )
    original_user_is_authenticated = models.BooleanField(
        default=False,
        help_text="Whether the originating user was authenticated during creation.",
    )

    objects = TodoManager()

    class Meta:
        verbose_name = "TODO"
        verbose_name_plural = "TODOs"
        constraints = [
            models.UniqueConstraint(
                Lower("request"),
                condition=Q(is_deleted=False),
                name="unique_active_todo_request",
            )
        ]

    def clean(self):
        super().clean()
        if (
            Todo.objects.filter(request__iexact=self.request, is_deleted=False)
            .exclude(pk=self.pk)
            .exists()
        ):
            raise ValidationError({"request": "Similar TODO already exists."})

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.request

    def natural_key(self):
        """Use the request field as the natural key."""
        return (self.request,)

    natural_key.dependencies = []

    @property
    def is_stale(self) -> bool:
        return self.stale_on is not None

    @staticmethod
    def _parse_version_label(label: str) -> Version | None:
        trimmed = (label or "").strip()
        if not trimmed:
            return None
        candidate = trimmed
        if candidate.endswith("+"):
            candidate = candidate.rstrip("+").rstrip()
            if not candidate:
                return None
        try:
            return Version(candidate)
        except InvalidVersion:
            return None

    @classmethod
    def version_context(cls) -> tuple[str, Version | None, list[dict[str, object]]]:
        label = (cls.default_version() or "").strip()
        parsed = cls._parse_version_label(label)
        return label, parsed, cls.release_timeline()

    @classmethod
    def release_timeline(cls) -> list[dict[str, object]]:
        try:
            releases = (
                PackageRelease.objects.filter(
                    package__is_active=True, release_on__isnull=False
                )
                .order_by("release_on")
                .values("release_on", "version")
            )
        except DatabaseError:  # pragma: no cover - database unavailable
            return []

        timeline: list[dict[str, object]] = []
        for entry in releases:
            label = (entry.get("version") or "").strip()
            parsed = cls._parse_version_label(label)
            if parsed is None:
                continue
            timeline.append(
                {
                    "release_on": entry.get("release_on"),
                    "version": parsed,
                    "label": label,
                }
            )
        return timeline

    @staticmethod
    def _release_entry_for_created(
        timeline: list[dict[str, object]], created: datetime_datetime | None
    ) -> dict[str, object] | None:
        if not timeline or created is None:
            return None

        candidates = [
            entry
            for entry in timeline
            if entry.get("release_on") is not None
            and entry["release_on"] <= created
        ]
        if candidates:
            return candidates[-1]
        return timeline[0]

    def _resolve_origin_version(
        self, timeline: list[dict[str, object]]
    ) -> dict[str, object] | None:
        label = (self.version or "").strip()
        parsed = self._parse_version_label(label)
        if parsed is not None:
            return {"version": parsed, "label": label}

        entry = self._release_entry_for_created(timeline, self.created_on)
        if entry is None:
            return None
        return entry

    def refresh_version_state(
        self,
        *,
        current_label: str,
        current_version: Version | None,
        timeline: list[dict[str, object]],
        now: datetime_datetime | None = None,
    ) -> bool:
        resolved = self._resolve_origin_version(timeline)
        resolved_version = resolved.get("version") if resolved else None
        resolved_label = resolved.get("label") if resolved else ""
        normalized_current = (current_label or "").strip()

        if not resolved_label and normalized_current and not (self.version or "").strip():
            resolved_label = normalized_current
            if resolved_version is None:
                resolved_version = self._parse_version_label(resolved_label)

        if resolved_label and (self.version or "").strip() != resolved_label:
            type(self).all_objects.filter(pk=self.pk).update(version=resolved_label)
            self.version = resolved_label

        is_stale = False
        if resolved_version is not None and current_version is not None:
            is_stale = resolved_version < current_version

        timestamp = now or timezone.now()
        if is_stale:
            if self.stale_on is None:
                type(self).all_objects.filter(pk=self.pk).update(stale_on=timestamp)
                self.stale_on = timestamp
        elif self.stale_on is not None:
            type(self).all_objects.filter(pk=self.pk).update(stale_on=None)
            self.stale_on = None

        return is_stale

    @classmethod
    def refresh_active(
        cls, *, now: datetime_datetime | None = None
    ) -> list["Todo"]:
        current_label, current_version, timeline = cls.version_context()
        moment = now or timezone.now()
        todos = list(cls.objects.filter(is_deleted=False, done_on__isnull=True))
        active: list["Todo"] = []
        for todo in todos:
            if not todo.refresh_version_state(
                current_label=current_label,
                current_version=current_version,
                timeline=timeline,
                now=moment,
            ):
                active.append(todo)
        return active

    def check_on_done_condition(self) -> ConditionCheckResult:
        """Evaluate the ``on_done_condition`` field for this TODO."""

        field = self._meta.get_field("on_done_condition")
        if isinstance(field, ConditionTextField):
            return field.evaluate(self)
        return ConditionCheckResult(True, "")

    def save(self, *args, **kwargs):
        created = self.pk is None
        if created:
            default_version = self._default_version()
            if default_version and not (self.version or "").strip():
                self.version = default_version
        tracked_fields = {
            "done_on",
            "done_node",
            "done_node_id",
            "done_revision",
            "done_username",
            "done_version",
            "is_deleted",
            "version",
        }
        update_fields = kwargs.get("update_fields")
        monitor_changes = not created and (
            update_fields is None or tracked_fields.intersection(update_fields)
        )
        previous_state = None
        previous_version_value = None
        if monitor_changes:
            previous_state = (
                type(self)
                .all_objects.filter(pk=self.pk)
                .values(
                    "done_on",
                    "done_node_id",
                    "done_revision",
                    "done_username",
                    "done_version",
                    "is_deleted",
                    "version",
                )
                .first()
            )
            if previous_state is not None:
                previous_version_value = previous_state.get("version")
        needs_version_refresh = created
        if not created and previous_state is not None:
            if (previous_version_value or "").strip() != (self.version or "").strip():
                needs_version_refresh = True
        elif not created and update_fields and "version" in update_fields:
            needs_version_refresh = True

        super().save(*args, **kwargs)

        if needs_version_refresh:
            current_label, current_version, timeline = type(self).version_context()
            self.refresh_version_state(
                current_label=current_label,
                current_version=current_version,
                timeline=timeline,
            )

        if created:
            return

        previous_done_on = previous_state["done_on"] if previous_state else None
        previous_is_deleted = previous_state["is_deleted"] if previous_state else False
        previous_done_node = (
            previous_state["done_node_id"] if previous_state else None
        )
        previous_done_revision = (
            previous_state["done_revision"] if previous_state else ""
        )
        previous_done_username = (
            previous_state["done_username"] if previous_state else ""
        )
        previous_done_version = (
            previous_state["done_version"] if previous_state else ""
        )
        if (
            previous_done_on == self.done_on
            and previous_is_deleted == self.is_deleted
            and previous_done_node == getattr(self, "done_node_id", None)
            and previous_done_revision == self.done_revision
            and previous_done_username == self.done_username
            and previous_done_version == self.done_version
        ):
            return

        self._update_fixture_state()

    def populate_done_metadata(self, user=None) -> None:
        """Populate metadata fields for a completed TODO."""

        node = None
        try:  # pragma: no cover - defensive import guard
            from nodes.models import Node  # type: ignore
        except Exception:  # pragma: no cover - when app not ready
            Node = None

        if Node is not None:
            try:
                node = Node.get_local()
            except Exception:  # pragma: no cover - fallback on errors
                node = None
        self.done_node = node if node else None

        version_value = ""
        revision_value = ""
        if node is not None:
            version_value = (node.installed_version or "").strip()
            revision_value = (node.installed_revision or "").strip()

        if not version_value:
            version_path = Path(settings.BASE_DIR) / "VERSION"
            try:
                version_value = version_path.read_text(encoding="utf-8").strip()
            except OSError:
                version_value = ""

        if not revision_value:
            try:
                revision_value = revision_utils.get_revision() or ""
            except Exception:  # pragma: no cover - defensive fallback
                revision_value = ""

        username_value = ""
        if user is not None and getattr(user, "is_authenticated", False):
            try:
                username_value = user.get_username() or ""
            except Exception:  # pragma: no cover - fallback to attribute
                username_value = getattr(user, "username", "") or ""

        self.done_version = version_value
        self.done_revision = revision_value
        self.done_username = username_value

    @staticmethod
    def _default_version() -> str:
        """Return the local version label used for TODO tracking."""

        try:  # pragma: no cover - defensive import guard
            from nodes.models import Node  # type: ignore
        except Exception:  # pragma: no cover - nodes app unavailable
            Node = None

        version_value = ""
        if Node is not None:
            try:
                node = Node.get_local()
            except Exception:  # pragma: no cover - unable to resolve node
                node = None
            if node is not None:
                version_value = (node.installed_version or "").strip()
                if version_value:
                    return version_value

        version_path = Path(settings.BASE_DIR) / "VERSION"
        try:
            version_value = version_path.read_text(encoding="utf-8").strip()
        except OSError:
            version_value = ""

        return version_value

    @classmethod
    def default_version(cls) -> str:
        """Public helper returning the tracked version label."""

        return cls._default_version()

    def _update_fixture_state(self) -> None:
        if not self.is_seed_data:
            return

        request_text = (self.request or "").strip()
        if not request_text:
            return

        slug = self._fixture_slug(request_text)
        if not slug:
            return

        base_dir = Path(settings.BASE_DIR)
        fixture_path = base_dir / "core" / "fixtures" / f"todo__{slug}.json"
        if not fixture_path.exists():
            return

        try:
            with fixture_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            logger.exception("Failed to read TODO fixture %s", fixture_path)
            return

        if not isinstance(data, list):
            return

        updated = False
        normalized_request = request_text.lower()
        for item in data:
            if not isinstance(item, dict):
                continue
            fields = item.get("fields")
            if not isinstance(fields, dict):
                continue
            candidate = (fields.get("request") or "").strip().lower()
            if candidate != normalized_request:
                continue
            if self._apply_fixture_fields(fields):
                updated = True

        if not updated:
            return

        content = json.dumps(data, indent=2, ensure_ascii=False)
        if not content.endswith("\n"):
            content += "\n"

        try:
            fixture_path.write_text(content, encoding="utf-8")
        except OSError:
            logger.exception("Failed to write TODO fixture %s", fixture_path)

    def _apply_fixture_fields(self, fields: dict[str, object]) -> bool:
        changed = False

        def _assign(key: str, value: object) -> None:
            nonlocal changed
            if fields.get(key) != value:
                fields[key] = value
                changed = True

        _assign("request", self.request or "")
        _assign("url", self.url or "")
        _assign("request_details", self.request_details or "")
        _assign("version", self.version or "")
        _assign("done_version", self.done_version or "")
        _assign("done_revision", self.done_revision or "")
        _assign("done_username", self.done_username or "")

        if self.stale_on:
            stale_value = timezone.localtime(self.stale_on)
            _assign("stale_on", stale_value.isoformat())
        else:
            if fields.get("stale_on") is not None:
                fields["stale_on"] = None
                changed = True

        if self.done_on:
            done_value = timezone.localtime(self.done_on)
            _assign("done_on", done_value.isoformat())
        else:
            if fields.get("done_on") is not None:
                fields["done_on"] = None
                changed = True

        if self.is_deleted:
            _assign("is_deleted", True)
        elif fields.get("is_deleted"):
            fields["is_deleted"] = False
            changed = True

        return changed

    @staticmethod
    def _fixture_slug(value: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
        return slug


class TOTPDeviceSettings(models.Model):
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
    is_seed_data = models.BooleanField(default=False)
    is_user_data = models.BooleanField(default=False)

    class Meta:
        verbose_name = _("Authenticator Device Setting")
        verbose_name_plural = _("Authenticator Device Settings")
