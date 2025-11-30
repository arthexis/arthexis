from __future__ import annotations

import json
import logging

from defusedxml import xmlrpc as defused_xmlrpc
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.template.defaultfilters import linebreaks
from django.urls import reverse
from django.utils import timezone
from django.utils.html import conditional_escape, format_html
from django.utils.translation import gettext, gettext_lazy as _

from apps.chats.models import ChatBridge, ChatBridgeManager
from apps.core.entity import Entity
from apps.core.models import Profile
from apps.sigils.fields import SigilShortAutoField


defused_xmlrpc.monkey_patch()
xmlrpc_client = defused_xmlrpc.xmlrpc_client

logger = logging.getLogger(__name__)


class OdooProduct(Entity):
    """A product defined in Odoo that users can subscribe to."""

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
        verbose_name = _("Odoo Product")
        verbose_name_plural = _("Odoo Products")
        db_table = "core_odoo_product"


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
    partner_id = models.PositiveIntegerField(null=True, blank=True, editable=False)

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
        verbose_name = _("Odoo Profile")
        verbose_name_plural = _("Odoo Profiles")
        db_table = "core_odooprofile"
        constraints = [
            models.CheckConstraint(
                condition=(
                    (Q(user__isnull=False) & Q(group__isnull=True))
                    | (Q(user__isnull=True) & Q(group__isnull=False))
                ),
                name="odooprofile_requires_owner",
            )
        ]


class OdooChatBridge(ChatBridge):
    """Configuration for forwarding visitor chat messages to Odoo."""

    site = models.ForeignKey(
        "sites.Site",
        on_delete=models.CASCADE,
        related_name="odoo_chat_bridges",
        null=True,
        blank=True,
        help_text=_(
            "Restrict this bridge to a specific site. Leave blank to use it as a fallback."
        ),
    )
    profile = models.ForeignKey(
        "odoo.OdooProfile",
        on_delete=models.CASCADE,
        related_name="chat_bridges",
        help_text=_("Verified Odoo employee credentials used to post chat messages."),
    )
    channel_id = models.PositiveIntegerField(
        help_text=_(
            "Identifier of the Odoo mail.channel that should receive forwarded messages."
        ),
        verbose_name=_("Channel ID"),
    )
    channel_uuid = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("Optional UUID of the Odoo mail.channel for reference."),
        verbose_name=_("Channel UUID"),
    )
    notify_partner_ids = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "Additional Odoo partner IDs to notify when posting messages. Provide a JSON array of integers."
        ),
    )

    objects = ChatBridgeManager()

    default_site_error_message = _(
        "Default Odoo chat bridges cannot target a specific site."
    )

    class Meta:
        ordering = ["site__domain", "pk"]
        verbose_name = _("Odoo Chat Bridge")
        verbose_name_plural = _("Odoo Chat Bridges")
        db_table = "pages_odoochatbridge"
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=Q(site__isnull=False),
                name="unique_odoo_chat_bridge_site",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="single_default_odoo_chat_bridge",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.site_id and self.site:
            return _("%(site)s → Odoo channel %(channel)s") % {
                "site": self.site,
                "channel": self.channel_id,
            }
        if self.is_default:
            return _("Default Odoo chat bridge (%(channel)s)") % {
                "channel": self.channel_id
            }
        return str(self.channel_id)

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if self.channel_id and self.channel_id <= 0:
            errors.setdefault("channel_id", []).append(
                _("Provide the numeric identifier of the Odoo mail channel."),
            )
        try:
            normalized = self._normalize_partner_ids(self.notify_partner_ids)
        except ValidationError as exc:
            raise exc
        else:
            self.notify_partner_ids = normalized
        if errors:
            raise ValidationError(errors)

    def partner_ids(self) -> list[int]:
        """Return the Odoo partner IDs that should be notified."""

        partner_ids: list[int] = []
        profile_partner = getattr(self.profile, "partner_id", None)
        if profile_partner:
            try:
                parsed = int(profile_partner)
            except (TypeError, ValueError):
                parsed = None
            else:
                if parsed > 0:
                    partner_ids.append(parsed)
        for ident in self.notify_partner_ids or []:
            try:
                parsed = int(ident)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in partner_ids:
                partner_ids.append(parsed)
        return partner_ids

    def post_message(self, session, message) -> bool:
        """Relay ``message`` to the configured Odoo channel."""

        if not self.is_enabled:
            return False
        if not self.profile or not self.profile.is_verified:
            return False
        content = (getattr(message, "body", "") or "").strip()
        if not content:
            return False
        subject = gettext("Visitor chat %(uuid)s") % {"uuid": getattr(session, "uuid", "")}
        body = self._render_body(session, message, content)
        payload: dict[str, object] = {
            "body": body,
            "subject": subject,
            "message_type": "comment",
            "subtype_xmlid": "mail.mt_comment",
        }
        partners = self.partner_ids()
        if partners:
            payload["partner_ids"] = partners
        try:
            self.profile.execute(
                "mail.channel",
                "message_post",
                [self.channel_id],
                payload,
            )
        except Exception:
            logger.exception(
                "Failed to forward chat message %s for session %s to Odoo channel %s",
                getattr(message, "pk", None),
                getattr(session, "pk", None),
                self.channel_id,
            )
            return False
        return True

    def _render_body(self, session, message, content: str) -> str:
        author = conditional_escape(message.author_label())
        body_content = linebreaks(content)
        metadata_parts: list[str] = []
        if getattr(session, "site_id", None) and getattr(session, "site", None):
            metadata_parts.append(str(session.site))
        if getattr(session, "pk", None):
            try:
                admin_path = reverse("admin:pages_chatsession_change", args=[session.pk])
            except Exception:
                admin_path = ""
            else:
                metadata_parts.append(gettext("Admin: %(path)s") % {"path": admin_path})
        metadata_parts.append(
            gettext("Author: %(label)s")
            % {"label": gettext("Staff") if getattr(message, "from_staff", False) else gettext("Visitor")}
        )
        timestamp = getattr(message, "created_at", None)
        if timestamp:
            try:
                display_ts = timezone.localtime(timestamp)
            except (TypeError, ValueError, AttributeError):
                display_ts = timestamp
            metadata_parts.append(display_ts.strftime("%Y-%m-%d %H:%M:%S %Z").strip())
        metadata_parts.append(str(getattr(session, "uuid", "")))
        meta_text = " • ".join(part for part in metadata_parts if part)
        return format_html(
            "<p><strong>{author}</strong></p>{content}<p><small>{meta}</small></p>",
            author=author,
            content=body_content,
            meta=meta_text,
        )

    def _normalize_partner_ids(self, values: object) -> list[int]:
        if not values:
            return []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError as exc:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be provided as a JSON array of integers.")}
                ) from exc
        if not isinstance(values, list):
            raise ValidationError(
                {"notify_partner_ids": _("Partner IDs must be provided as a list of integers.")}
            )
        normalized: list[int] = []
        for item in values:
            if item in (None, ""):
                continue
            try:
                ident = int(item)
            except (TypeError, ValueError) as exc:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be integers.")}
                ) from exc
            if ident <= 0:
                raise ValidationError(
                    {"notify_partner_ids": _("Partner IDs must be positive integers.")}
                )
            if ident not in normalized:
                normalized.append(ident)
        return normalized
