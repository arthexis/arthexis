from __future__ import annotations

import json
import logging

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.template.defaultfilters import linebreaks
from django.urls import reverse
from django.utils import timezone
from django.utils.html import conditional_escape, format_html
from django.utils.translation import gettext, gettext_lazy as _

from apps.chats.models import ChatBridge, ChatBridgeManager

logger = logging.getLogger(__name__)


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
        "odoo.OdooEmployee",
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
