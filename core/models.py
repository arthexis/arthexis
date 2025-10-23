from django.contrib.auth.models import (
    AbstractUser,
    Group,
    UserManager as DjangoUserManager,
)
from django.db import DatabaseError, IntegrityError, connections, models, transaction
from django.db.models import Q
from django.db.models.functions import Lower, Length
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.core.exceptions import ValidationError
from django.apps import apps
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.views.decorators.debug import sensitive_variables
from datetime import time as datetime_time, timedelta
import logging
from django.contrib.contenttypes.models import ContentType
import hashlib
import hmac
import os
import subprocess
import secrets
import re
from io import BytesIO
from django.core.files.base import ContentFile
import qrcode
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import uuid
from pathlib import Path
from django.core import serializers
from django.core.management.color import no_style
from urllib.parse import quote_plus, urlparse
from utils import revision as revision_utils
from typing import Any, Type
from defusedxml import xmlrpc as defused_xmlrpc
import requests

defused_xmlrpc.monkey_patch()
xmlrpc_client = defused_xmlrpc.xmlrpc_client

logger = logging.getLogger(__name__)

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
        "nodes.EmailOutbox",
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
    birthday = models.DateField(null=True, blank=True)
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
            "Designates whether this user should be treated as active. Unselect this instead of deleting energy accounts."
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
    def assistant_profile(self):
        return self._direct_profile("AssistantProfile")

    @property
    def social_profile(self):
        return self._direct_profile("SocialProfile")

    @property
    def chat_profile(self):
        return self.assistant_profile


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

    profile_fields = ("host", "database", "username", "password")
    host = SigilShortAutoField(max_length=255)
    database = SigilShortAutoField(max_length=255)
    username = SigilShortAutoField(max_length=255)
    password = SigilShortAutoField(max_length=255)
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
        computed_name = self._display_identifier()
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
        verbose_name = _("Odoo Employee")
        verbose_name_plural = _("Odoo Employees")
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
    """Store OpenPay gateway credentials for a user or security group."""

    SANDBOX_API_URL = "https://sandbox-api.openpay.mx/v1"
    PRODUCTION_API_URL = "https://api.openpay.mx/v1"

    profile_fields = (
        "merchant_id",
        "private_key",
        "public_key",
        "is_production",
        "webhook_secret",
    )

    merchant_id = SigilShortAutoField(max_length=100)
    private_key = SigilShortAutoField(max_length=255)
    public_key = SigilShortAutoField(max_length=255)
    is_production = models.BooleanField(default=False)
    webhook_secret = SigilShortAutoField(max_length=255, blank=True)
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
            ):
                self._clear_verification()
        super().save(*args, **kwargs)

    @property
    def is_verified(self):
        return self.verified_on is not None

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

    def verify(self):
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

    def __str__(self):  # pragma: no cover - simple representation
        owner = self.owner_display()
        return f"{owner} @ {self.merchant_id}" if owner else self.merchant_id

    class Meta:
        verbose_name = _("OpenPay Merchant")
        verbose_name_plural = _("OpenPay Merchants")
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="openpayprofile_requires_owner",
            )
        ]


class EmailInbox(Profile):
    """Credentials and configuration for connecting to an email mailbox."""

    IMAP = "imap"
    POP3 = "pop3"
    PROTOCOL_CHOICES = [
        (IMAP, "IMAP"),
        (POP3, "POP3"),
    ]

    profile_fields = (
        "username",
        "host",
        "port",
        "password",
        "protocol",
        "use_ssl",
    )
    username = SigilShortAutoField(
        max_length=255,
        help_text="Login name for the mailbox",
    )
    host = SigilShortAutoField(
        max_length=255,
        help_text=(
            "Examples: Gmail IMAP 'imap.gmail.com', Gmail POP3 'pop.gmail.com',"
            " GoDaddy IMAP 'imap.secureserver.net', GoDaddy POP3 'pop.secureserver.net'"
        ),
    )
    port = models.PositiveIntegerField(
        default=993,
        help_text=(
            "Common ports: Gmail IMAP 993, Gmail POP3 995, "
            "GoDaddy IMAP 993, GoDaddy POP3 995"
        ),
    )
    password = SigilShortAutoField(max_length=255)
    protocol = SigilShortAutoField(
        max_length=5,
        choices=PROTOCOL_CHOICES,
        default=IMAP,
        help_text=(
            "IMAP keeps emails on the server for access across devices; "
            "POP3 downloads messages to a single device and may remove them from the server"
        ),
    )
    use_ssl = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Email Inbox"
        verbose_name_plural = "Email Inboxes"

    def test_connection(self):
        """Attempt to connect to the configured mailbox."""
        try:
            if self.protocol == self.IMAP:
                import imaplib

                conn = (
                    imaplib.IMAP4_SSL(self.host, self.port)
                    if self.use_ssl
                    else imaplib.IMAP4(self.host, self.port)
                )
                conn.login(self.username, self.password)
                conn.logout()
            else:
                import poplib

                conn = (
                    poplib.POP3_SSL(self.host, self.port)
                    if self.use_ssl
                    else poplib.POP3(self.host, self.port)
                )
                conn.user(self.username)
                conn.pass_(self.password)
                conn.quit()
            return True
        except Exception as exc:
            raise ValidationError(str(exc))

    def search_messages(
        self,
        subject="",
        from_address="",
        body="",
        limit: int = 10,
        use_regular_expressions: bool = False,
    ):
        """Retrieve up to ``limit`` recent messages matching the filters.

        Parameters are case-insensitive fragments by default. When
        ``use_regular_expressions`` is ``True`` the filters are treated as regular
        expressions using case-insensitive matching. Results are returned as a
        list of dictionaries with ``subject``, ``from``, ``body`` and ``date``
        keys.
        """

        def _compile(pattern: str | None):
            if not pattern:
                return None
            try:
                return re.compile(pattern, re.IGNORECASE)
            except re.error as exc:
                raise ValidationError(str(exc))

        subject_regex = sender_regex = body_regex = None
        if use_regular_expressions:
            subject_regex = _compile(subject)
            sender_regex = _compile(from_address)
            body_regex = _compile(body)

        def _matches(value: str, needle: str, regex):
            value = value or ""
            if regex is not None:
                return bool(regex.search(value))
            if not needle:
                return True
            return needle.lower() in value.lower()

        from email.header import decode_header

        def _get_body(msg):
            if msg.is_multipart():
                for part in msg.walk():
                    if (
                        part.get_content_type() == "text/plain"
                        and not part.get_filename()
                    ):
                        charset = part.get_content_charset() or "utf-8"
                        return part.get_payload(decode=True).decode(
                            charset, errors="ignore"
                        )
                return ""
            charset = msg.get_content_charset() or "utf-8"
            return msg.get_payload(decode=True).decode(charset, errors="ignore")

        def _decode_header_value(value):
            if not value:
                return ""
            if isinstance(value, bytes):
                value = value.decode("utf-8", errors="ignore")
            try:
                parts = decode_header(value)
            except Exception:
                return value if isinstance(value, str) else ""
            decoded = []
            for text, encoding in parts:
                if isinstance(text, bytes):
                    encodings_to_try = []
                    if encoding:
                        encodings_to_try.append(encoding)
                    encodings_to_try.extend(["utf-8", "latin-1"])
                    for candidate in encodings_to_try:
                        try:
                            decoded.append(
                                text.decode(candidate, errors="ignore")
                            )
                            break
                        except LookupError:
                            continue
                    else:
                        try:
                            decoded.append(text.decode("utf-8", errors="ignore"))
                        except Exception:
                            decoded.append("")
                else:
                    decoded.append(text)
            return "".join(decoded)

        if self.protocol == self.IMAP:
            import imaplib
            import email

            def _decode_imap_bytes(value):
                if isinstance(value, bytes):
                    return value.decode("utf-8", errors="ignore")
                return str(value)

            conn = (
                imaplib.IMAP4_SSL(self.host, self.port)
                if self.use_ssl
                else imaplib.IMAP4(self.host, self.port)
            )
            try:
                conn.login(self.username, self.password)
                typ, data = conn.select("INBOX")
                if typ != "OK":
                    message = " ".join(_decode_imap_bytes(item) for item in data or [])
                    if not message:
                        message = "Unable to select INBOX"
                    raise ValidationError(message)

                fetch_limit = (
                    limit if not use_regular_expressions else max(limit * 5, limit)
                )
                if use_regular_expressions:
                    typ, data = conn.search(None, "ALL")
                else:
                    criteria = []
                    charset = None

                    def _quote_bytes(raw: bytes) -> bytes:
                        return b'"' + raw.replace(b"\\", b"\\\\").replace(b'"', b'\\"') + b'"'

                    def _append(term: str, value: str):
                        nonlocal charset
                        if not value:
                            return
                        try:
                            value.encode("ascii")
                            encoded_value = value
                        except UnicodeEncodeError:
                            charset = charset or "UTF-8"
                            encoded_value = _quote_bytes(value.encode("utf-8"))
                        else:
                            # Quote ASCII strings only when they include whitespace to
                            # avoid breaking atoms while keeping backward-compatible
                            # behaviour for simple searches.
                            if any(ch.isspace() for ch in value):
                                encoded_value = value.replace("\\", "\\\\").replace(
                                    '"', '\\"'
                                )
                                encoded_value = f'"{encoded_value}"'
                        criteria.extend([term, encoded_value])

                    _append("SUBJECT", subject)
                    _append("FROM", from_address)
                    _append("TEXT", body)

                    if not criteria:
                        typ, data = conn.search(None, "ALL")
                    else:
                        typ, data = conn.search(charset, *criteria)

                if typ != "OK":
                    message = " ".join(_decode_imap_bytes(item) for item in data or [])
                    if not message:
                        message = "Unable to search mailbox"
                    raise ValidationError(message)

                ids = data[0].split()[-fetch_limit:]
                messages = []
                for mid in ids:
                    typ, msg_data = conn.fetch(mid, "(RFC822)")
                    if typ != "OK" or not msg_data:
                        continue
                    msg = email.message_from_bytes(msg_data[0][1])
                    body_text = _get_body(msg)
                    subj_value = _decode_header_value(msg.get("Subject", ""))
                    from_value = _decode_header_value(msg.get("From", ""))
                    if not (
                        _matches(subj_value, subject, subject_regex)
                        and _matches(from_value, from_address, sender_regex)
                        and _matches(body_text, body, body_regex)
                    ):
                        continue
                    messages.append(
                        {
                            "subject": subj_value,
                            "from": from_value,
                            "body": body_text,
                            "date": msg.get("Date", ""),
                        }
                    )
                    if len(messages) >= limit:
                        break
                return list(reversed(messages))
            finally:
                try:
                    conn.logout()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass

        import poplib
        import email

        conn = (
            poplib.POP3_SSL(self.host, self.port)
            if self.use_ssl
            else poplib.POP3(self.host, self.port)
        )
        conn.user(self.username)
        conn.pass_(self.password)
        count = len(conn.list()[1])
        messages = []
        for i in range(count, 0, -1):
            resp, lines, octets = conn.retr(i)
            msg = email.message_from_bytes(b"\n".join(lines))
            subj = _decode_header_value(msg.get("Subject", ""))
            frm = _decode_header_value(msg.get("From", ""))
            body_text = _get_body(msg)
            if not (
                _matches(subj, subject, subject_regex)
                and _matches(frm, from_address, sender_regex)
                and _matches(body_text, body, body_regex)
            ):
                continue
            messages.append(
                {
                    "subject": subj,
                    "from": frm,
                    "body": body_text,
                    "date": msg.get("Date", ""),
                }
            )
            if len(messages) >= limit:
                break
        conn.quit()
        return messages

    def __str__(self):  # pragma: no cover - simple representation
        username = (self.username or "").strip()
        host = (self.host or "").strip()

        if username:
            if "@" in username:
                return username
            if host:
                return f"{username}@{host}"
            return username

        if host:
            return host

        owner = self.owner_display()
        if owner:
            return owner

        return super().__str__()


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

    def clean(self):
        super().clean()
        errors = {}
        if self.network == self.Network.BLUESKY:
            if not self.handle:
                errors["handle"] = _("Provide the handle that should point to this domain.")
            if not self.domain:
                errors["domain"] = _("A verified domain is required for Bluesky handles.")
        elif self.network == self.Network.DISCORD:
            if not self.application_id:
                errors["application_id"] = _("Provide the Discord application ID for the bot.")
            if not self.guild_id:
                errors["guild_id"] = _("Provide the Discord guild identifier where the bot will operate.")
            if not self.bot_token:
                errors["bot_token"] = _("Provide the Discord bot token so Arthexis can control the bot.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.handle:
            self.handle = self.handle.strip().lower()
        if self.domain:
            self.domain = self.domain.strip().lower()
        if self.did:
            self.did = self.did.strip()
        for attr in ("application_id", "public_key", "guild_id", "default_channel_id"):
            value = getattr(self, attr)
            if value:
                setattr(self, attr, value.strip())
        super().save(*args, **kwargs)

    def __str__(self):  # pragma: no cover - simple representation
        handle = (
            self.resolve_sigils("handle")
            or self.handle
            or self.domain
            or self.resolve_sigils("guild_id")
            or self.guild_id
            or self.resolve_sigils("application_id")
            or self.application_id
            or ""
        ).strip()
        network = (self.resolve_sigils("network") or self.network or "").strip()

        if handle.startswith("@"):
            handle = handle[1:]

        if handle and network:
            return f"{handle}@{network}"
        if handle:
            return handle
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


class EmailCollector(Entity):
    """Search an inbox for matching messages and extract data via sigils."""

    name = models.CharField(
        max_length=255,
        blank=True,
        help_text="Optional label to identify this collector.",
    )
    inbox = models.ForeignKey(
        "EmailInbox",
        related_name="collectors",
        on_delete=models.CASCADE,
    )
    subject = models.CharField(max_length=255, blank=True)
    sender = models.CharField(max_length=255, blank=True)
    body = models.CharField(max_length=255, blank=True)
    fragment = models.CharField(
        max_length=255,
        blank=True,
        help_text="Pattern with [sigils] to extract values from the body.",
    )
    use_regular_expressions = models.BooleanField(
        default=False,
        help_text="Treat subject, sender and body filters as regular expressions (case-insensitive).",
    )

    def _parse_sigils(self, text: str) -> dict[str, str]:
        """Extract values from ``text`` according to ``fragment`` sigils."""
        if not self.fragment:
            return {}
        import re

        parts = re.split(r"\[([^\]]+)\]", self.fragment)
        pattern = ""
        for idx, part in enumerate(parts):
            if idx % 2 == 0:
                pattern += re.escape(part)
            else:
                pattern += f"(?P<{part}>.+)"
        match = re.search(pattern, text)
        if not match:
            return {}
        return {k: v.strip() for k, v in match.groupdict().items()}

    def __str__(self):  # pragma: no cover - simple representation
        if self.name:
            return self.name
        parts = []
        if self.subject:
            parts.append(self.subject)
        if self.sender:
            parts.append(self.sender)
        if not parts:
            parts.append(str(self.inbox))
        return " – ".join(parts)

    def search_messages(self, limit: int = 10):
        return self.inbox.search_messages(
            subject=self.subject,
            from_address=self.sender,
            body=self.body,
            limit=limit,
            use_regular_expressions=self.use_regular_expressions,
        )

    def collect(self, limit: int = 10) -> None:
        """Poll the inbox and store new artifacts until an existing one is found."""
        from .models import EmailArtifact

        messages = self.search_messages(limit=limit)
        for msg in messages:
            fp = EmailArtifact.fingerprint_for(
                msg.get("subject", ""), msg.get("from", ""), msg.get("body", "")
            )
            if EmailArtifact.objects.filter(collector=self, fingerprint=fp).exists():
                break
            EmailArtifact.objects.create(
                collector=self,
                subject=msg.get("subject", ""),
                sender=msg.get("from", ""),
                body=msg.get("body", ""),
                sigils=self._parse_sigils(msg.get("body", "")),
                fingerprint=fp,
            )

    class Meta:
        verbose_name = _("Email Collector")
        verbose_name_plural = _("Email Collectors")


class EmailArtifact(Entity):
    """Store messages discovered by :class:`EmailCollector`."""

    collector = models.ForeignKey(
        EmailCollector, related_name="artifacts", on_delete=models.CASCADE
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
        "EmailCollector",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Collector that discovered this message, if applicable.",
    )
    inbox = models.ForeignKey(
        "EmailInbox",
        null=True,
        blank=True,
        related_name="transactions",
        on_delete=models.SET_NULL,
        help_text="Inbox account the message was read from or will use for sending.",
    )
    outbox = models.ForeignKey(
        "nodes.EmailOutbox",
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
        """Return the energy account associated with an RFID code if it exists."""
        try:
            EnergyAccount = apps.get_model("core", "EnergyAccount")
        except LookupError:  # pragma: no cover - energy accounts app optional
            return None
        matches = cls.matching_queryset(value).filter(allowed=True)
        if not matches.exists():
            return None
        return (
            EnergyAccount.objects.filter(rfids__in=matches)
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

class EnergyAccount(Entity):
    """Track kW energy credits for a user."""

    name = models.CharField(max_length=100, unique=True)
    user = models.OneToOneField(
        get_user_model(),
        on_delete=models.CASCADE,
        related_name="energy_account",
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
        return self.service_account or self.balance_kw > 0

    @property
    def credits_kw(self):
        """Total kW energy credits added to the energy account."""
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
        """Remaining kW available for the energy account."""
        return self.credits_kw - self.total_kw_spent

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
        verbose_name = "Energy Account"
        verbose_name_plural = "Energy Accounts"
        db_table = "core_account"


class EnergyCredit(Entity):
    """Energy credits added to an energy account."""

    account = models.ForeignKey(
        EnergyAccount, on_delete=models.CASCADE, related_name="credits"
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
            else f"Energy Account {self.account_id}"
        )
        return f"{self.amount_kw} kW for {user}"

    class Meta:
        verbose_name = "Energy Credit"
        verbose_name_plural = "Energy Credits"
        db_table = "core_credit"


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
    email_recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)
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

    def __str__(self) -> str:  # pragma: no cover - simple representation
        owner = self.owner.get_username() if self.owner else "Unassigned"
        return f"Client Report Schedule ({owner})"

    def save(self, *args, **kwargs):
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

        name = f"client_report_schedule_{self.pk}"
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

    def get_outbox(self):
        """Return the preferred :class:`nodes.models.EmailOutbox` instance."""

        from nodes.models import EmailOutbox, Node

        if self.owner:
            try:
                outbox = self.owner.get_profile(EmailOutbox)
            except Exception:  # pragma: no cover - defensive catch
                outbox = None
            if outbox:
                return outbox

        node = Node.get_local()
        if node:
            return getattr(node, "email_outbox", None)
        return None

    def notify_failure(self, message: str):
        from nodes.models import NetMessage

        NetMessage.broadcast("Client report delivery issue", message)

    def run(self):
        """Generate the report, persist it and deliver notifications."""

        from core import mailer

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
            )
            export, html_content = report.store_local_copy()
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
                    attachments = []
                    html_name = Path(export["html_path"]).name
                    attachments.append((html_name, html_content, "text/html"))
                    json_file = Path(settings.BASE_DIR) / export["json_path"]
                    if json_file.exists():
                        attachments.append(
                            (
                                json_file.name,
                                json_file.read_text(encoding="utf-8"),
                                "application/json",
                            )
                        )
                    subject = f"Client report {report.start_date} to {report.end_date}"
                    body = (
                        "Attached is the client report generated for the period "
                        f"{report.start_date} to {report.end_date}."
                    )
                    mailer.send(
                        subject,
                        body,
                        to,
                        outbox=self.get_outbox(),
                        cc=cc,
                        attachments=attachments,
                    )
                    delivered = list(dict.fromkeys(to + (cc or [])))
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
    recipients = models.JSONField(default=list, blank=True)
    disable_emails = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Consumer Report"
        verbose_name_plural = "Consumer Reports"
        db_table = "core_client_report"
        ordering = ["-created_on"]

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
    ):
        rows = cls.build_rows(start_date, end_date)
        return cls.objects.create(
            start_date=start_date,
            end_date=end_date,
            data={"rows": rows, "schema": "session-list/v1"},
            owner=owner,
            schedule=schedule,
            recipients=list(recipients or []),
            disable_emails=disable_emails,
        )

    def store_local_copy(self, html: str | None = None):
        """Persist the report data and optional HTML rendering to disk."""

        import json as _json
        from django.template.loader import render_to_string

        base_dir = Path(settings.BASE_DIR)
        report_dir = base_dir / "work" / "reports"
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d%H%M%S")
        identifier = f"client_report_{self.pk}_{timestamp}"

        html_content = html or render_to_string(
            "core/reports/client_report_email.html", {"report": self}
        )
        html_path = report_dir / f"{identifier}.html"
        html_path.write_text(html_content, encoding="utf-8")

        json_path = report_dir / f"{identifier}.json"
        json_path.write_text(
            _json.dumps(self.data, indent=2, default=str), encoding="utf-8"
        )

        def _relative(path: Path) -> str:
            try:
                return str(path.relative_to(base_dir))
            except ValueError:
                return str(path)

        export = {
            "html_path": _relative(html_path),
            "json_path": _relative(json_path),
        }

        updated = dict(self.data)
        updated["export"] = export
        type(self).objects.filter(pk=self.pk).update(data=updated)
        self.data = updated
        return export, html_content

    @staticmethod
    def build_rows(start_date=None, end_date=None, *, for_display: bool = False):
        from ocpp.models import Transaction

        qs = Transaction.objects.filter(
            (Q(rfid__isnull=False) & ~Q(rfid=""))
            | (Q(vid__isnull=False) & ~Q(vid=""))
        )
        if start_date:
            from datetime import datetime, time, timedelta, timezone as pytimezone

            start_dt = datetime.combine(start_date, time.min, tzinfo=pytimezone.utc)
            qs = qs.filter(start_time__gte=start_dt)
        if end_date:
            from datetime import datetime, time, timedelta, timezone as pytimezone

            end_dt = datetime.combine(
                end_date + timedelta(days=1), time.min, tzinfo=pytimezone.utc
            )
            qs = qs.filter(start_time__lt=end_dt)

        transactions = list(
            qs.select_related("account").order_by("-start_time", "-pk")
        )
        rfid_values = {tx.rfid for tx in transactions if tx.rfid}
        tag_map: dict[str, RFID] = {}
        if rfid_values:
            tag_map = {
                tag.rfid: tag
                for tag in RFID.objects.filter(rfid__in=rfid_values).prefetch_related(
                    "energy_accounts"
                )
            }

        rows: list[dict[str, Any]] = []
        for tx in transactions:
            energy = tx.kw
            if energy <= 0:
                continue

            subject = None
            if tx.account and getattr(tx.account, "name", None):
                subject = tx.account.name
            else:
                tag = tag_map.get(tx.rfid)
                if tag:
                    account = next(iter(tag.energy_accounts.all()), None)
                    if account:
                        subject = account.name
                    else:
                        subject = str(tag.label_id)

            if subject is None:
                subject = tx.rfid or tx.vid

            start_value = tx.start_time
            end_value = tx.stop_time
            if not for_display:
                start_value = start_value.isoformat()
                end_value = end_value.isoformat() if end_value else None

            rows.append(
                {
                    "subject": subject,
                    "rfid": tx.rfid,
                    "vid": tx.vid,
                    "kw": energy,
                    "start": start_value,
                    "end": end_value,
                }
            )

        return rows

    @property
    def rows_for_display(self):
        rows = self.data.get("rows", [])
        if self.data.get("schema") == "session-list/v1":
            parsed: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                start_val = row.get("start")
                end_val = row.get("end")

                if start_val:
                    start_dt = parse_datetime(start_val)
                    if start_dt and timezone.is_naive(start_dt):
                        start_dt = timezone.make_aware(start_dt, timezone.utc)
                    item["start"] = start_dt
                else:
                    item["start"] = None

                if end_val:
                    end_dt = parse_datetime(end_val)
                    if end_dt and timezone.is_naive(end_dt):
                        end_dt = timezone.make_aware(end_dt, timezone.utc)
                    item["end"] = end_dt
                else:
                    item["end"] = None

                parsed.append(item)
            return parsed
        return rows


class BrandManager(EntityManager):
    def get_by_natural_key(self, name: str):
        return self.get(name=name)


class Brand(Entity):
    """Vehicle manufacturer or brand."""

    name = models.CharField(max_length=100, unique=True)

    objects = BrandManager()

    class Meta:
        verbose_name = _("EV Brand")
        verbose_name_plural = _("EV Brands")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.name

    def natural_key(self):  # pragma: no cover - simple representation
        return (self.name,)

    @classmethod
    def from_vin(cls, vin: str) -> "Brand | None":
        """Return the brand matching the VIN's WMI prefix."""
        if not vin:
            return None
        prefix = vin[:3].upper()
        return cls.objects.filter(wmi_codes__code=prefix).first()


class WMICode(Entity):
    """World Manufacturer Identifier code for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="wmi_codes")
    code = models.CharField(max_length=3, unique=True)

    class Meta:
        verbose_name = _("WMI Code")
        verbose_name_plural = _("WMI Codes")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.code


class EVModel(Entity):
    """Specific electric vehicle model for a brand."""

    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="ev_models")
    name = models.CharField(max_length=100)
    battery_capacity_kwh = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Battery Capacity (kWh)",
    )
    est_battery_kwh = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Estimated Battery (kWh)",
    )
    ac_110v_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="110V AC (kW)",
    )
    ac_220v_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="220V AC (kW)",
    )
    dc_60_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="60kW DC (kW)",
    )
    dc_100_power_kw = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="100kW DC (kW)",
    )

    class Meta:
        unique_together = ("brand", "name")
        verbose_name = _("EV Model")
        verbose_name_plural = _("EV Models")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.brand} {self.name}" if self.brand else self.name


class ElectricVehicle(Entity):
    """Electric vehicle associated with an Energy Account."""

    account = models.ForeignKey(
        EnergyAccount, on_delete=models.CASCADE, related_name="vehicles"
    )
    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )
    model = models.ForeignKey(
        EVModel,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="vehicles",
    )
    vin = models.CharField(max_length=17, unique=True, verbose_name="VIN")
    license_plate = models.CharField(_("License Plate"), max_length=20, blank=True)

    def save(self, *args, **kwargs):
        if self.model and not self.brand:
            self.brand = self.model.brand
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        brand_name = self.brand.name if self.brand else ""
        model_name = self.model.name if self.model else ""
        parts = " ".join(p for p in [brand_name, model_name] if p)
        return f"{parts} ({self.vin})" if parts else self.vin

    class Meta:
        verbose_name = _("Electric Vehicle")
        verbose_name_plural = _("Electric Vehicles")


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
            data = serializers.serialize("json", [release])
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

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.package.name} {self.version}"

    def to_package(self) -> ReleasePackage:
        """Return a :class:`ReleasePackage` built from the package."""
        return self.package.to_package()

    def to_credentials(self) -> Credentials | None:
        """Return :class:`Credentials` from the associated release manager."""
        manager = self.release_manager or self.package.release_manager
        if manager:
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

    def build_publish_targets(self) -> list[RepositoryTarget]:
        """Return repository targets for publishing this release."""

        manager = self.release_manager or self.package.release_manager
        targets: list[RepositoryTarget] = []

        env_primary = os.environ.get("PYPI_REPOSITORY_URL", "")
        primary_url = env_primary.strip()

        primary_creds = self.to_credentials()
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


# Ensure each RFID can only be linked to one energy account
@receiver(m2m_changed, sender=EnergyAccount.rfids.through)
def _rfid_unique_energy_account(
    sender, instance, action, reverse, model, pk_set, **kwargs
):
    """Prevent associating an RFID with more than one energy account."""
    if action == "pre_add":
        if reverse:  # adding energy accounts to an RFID
            if instance.energy_accounts.exclude(pk__in=pk_set).exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one energy account."
                )
        else:  # adding RFIDs to an energy account
            conflict = model.objects.filter(
                pk__in=pk_set, energy_accounts__isnull=False
            ).exclude(energy_accounts=instance)
            if conflict.exists():
                raise ValidationError(
                    "RFID tags may only be assigned to one energy account."
                )


def hash_key(key: str) -> str:
    """Return a SHA-256 hash for ``key``."""

    return hashlib.sha256(key.encode()).hexdigest()


class AssistantProfile(Profile):
    """Stores a hashed user key used by the assistant for authentication.

    The plain-text ``user_key`` is generated server-side and shown only once.
    Users must supply this key in the ``Authorization: Bearer <user_key>``
    header when requesting protected endpoints. Only the hash is stored.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    profile_fields = ("assistant_name", "user_key_hash", "scopes", "is_active")
    assistant_name = models.CharField(max_length=100, default="Assistant")
    user_key_hash = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "workgroup_assistantprofile"
        verbose_name = "Assistant Profile"
        verbose_name_plural = "Assistant Profiles"
        constraints = [
            models.CheckConstraint(
                check=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="assistantprofile_requires_owner",
            )
        ]

    @classmethod
    def issue_key(cls, user) -> tuple["AssistantProfile", str]:
        """Create or update a profile and return it with a new plain key."""

        key = secrets.token_hex(32)
        key_hash = hash_key(key)
        if user is None:
            raise ValueError("Assistant profiles require a user instance")

        profile, _ = cls.objects.update_or_create(
            user=user,
            defaults={
                "user_key_hash": key_hash,
                "last_used_at": None,
                "is_active": True,
            },
        )
        return profile, key

    def touch(self) -> None:
        """Record that the key was used."""

        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.assistant_name or "AssistantProfile"


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
    url = models.CharField(
        max_length=200, blank=True, default="", validators=[validate_relative_url]
    )
    request_details = models.TextField(blank=True, default="")
    generated_for_version = models.CharField(max_length=20, blank=True, default="")
    generated_for_revision = models.CharField(max_length=40, blank=True, default="")
    done_on = models.DateTimeField(null=True, blank=True)
    on_done_condition = ConditionTextField(blank=True, default="")

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

    def check_on_done_condition(self) -> ConditionCheckResult:
        """Evaluate the ``on_done_condition`` field for this TODO."""

        field = self._meta.get_field("on_done_condition")
        if isinstance(field, ConditionTextField):
            return field.evaluate(self)
        return ConditionCheckResult(True, "")


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
        verbose_name = _("Authenticator device settings")
        verbose_name_plural = _("Authenticator device settings")
