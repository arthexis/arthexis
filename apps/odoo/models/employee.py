from __future__ import annotations

import logging

from defusedxml import xmlrpc as defused_xmlrpc
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.chats.models import ChatAvatar
from apps.users.models import Profile
from apps.sigils.fields import SigilShortAutoField


defused_xmlrpc.monkey_patch()
xmlrpc_client = defused_xmlrpc.xmlrpc_client

logger = logging.getLogger(__name__)


class OdooEmployee(Profile):
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
    partner_id = models.PositiveIntegerField(null=True, blank=True, editable=False)
    avatar = models.ForeignKey(
        ChatAvatar,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="odoo_profiles",
        help_text=_("Chat avatar that owns these Odoo credentials."),
    )

    def _clear_verification(self):
        self.verified_on = None
        self.odoo_uid = None
        self.name = ""
        self.email = ""
        self.partner_id = None

    def _resolved_field_value(self, field: str) -> str:
        """Return the resolved value for ``field`` falling back to raw data."""

        resolved = self.resolve_sigils(field)
        if resolved:
            return resolved
        value = getattr(self, field, "")
        return value or ""

    def _display_identifier(self) -> str:
        """Return the display label for this profile."""

        if self.name:
            return self.name
        username = self._resolved_field_value("username")
        if username:
            return username
        return self._resolved_field_value("database")

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

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.avatar_id:
            errors.setdefault("avatar", []).append(
                _("Select an avatar to own these credentials."),
            )
        if self.user_id or self.group_id:
            errors.setdefault("avatar", []).append(
                _("Assign the profile to an avatar instead of a direct user or group."),
            )
        if errors:
            raise ValidationError(errors)

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
            {"fields": ["name", "email", "partner_id"]},
        )[0]
        self.odoo_uid = uid
        self.email = info.get("email", "")
        self.verified_on = timezone.now()
        partner_info = info.get("partner_id")
        partner_id: int | None = None
        if isinstance(partner_info, (list, tuple)) and partner_info:
            try:
                partner_id = int(partner_info[0])
            except (TypeError, ValueError):
                partner_id = None
        elif isinstance(partner_info, int):
            partner_id = partner_info
        self.partner_id = partner_id
        self.name = self._profile_name()
        self.save(
            update_fields=[
                "odoo_uid",
                "name",
                "email",
                "verified_on",
                "partner_id",
            ]
        )
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
            self.save(
                update_fields=[
                    "verified_on",
                    "odoo_uid",
                    "name",
                    "email",
                    "partner_id",
                ]
            )
            raise

    def __str__(self):  # pragma: no cover - simple representation
        username = self._resolved_field_value("username")
        if username:
            return username
        label = self._display_identifier()
        if label:
            return label
        owner = self.owner_display()
        return f"{owner} @ {self.host}" if owner else self.host

    class Meta:
        verbose_name = _("Odoo Employee")
        verbose_name_plural = _("Odoo Employees")
        db_table = "core_odooemployee"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(avatar__isnull=True)
                    | (
                        Q(avatar__isnull=False)
                        & Q(user__isnull=True)
                        & Q(group__isnull=True)
                    )
                ),
                name="odooemployee_avatar_exclusive",
            )
        ]
