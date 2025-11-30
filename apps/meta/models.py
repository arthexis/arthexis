from __future__ import annotations

import contextlib
import logging

import requests
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext, gettext_lazy as _

from apps.chats.models import ChatBridge, ChatBridgeManager


logger = logging.getLogger(__name__)


class WhatsAppChatBridge(ChatBridge):
    """Configuration for forwarding chat messages to WhatsApp."""

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="whatsapp_chat_bridges",
        null=True,
        blank=True,
        help_text=_(
            "Restrict this bridge to a specific site. Leave blank to use it as a fallback."
        ),
    )
    api_base_url = models.URLField(
        default="https://graph.facebook.com/v18.0",
        help_text=_("Base URL for the Meta Graph API."),
    )
    phone_number_id = models.CharField(
        max_length=64,
        help_text=_("Identifier of the WhatsApp phone number used for delivery."),
        verbose_name=_("Phone Number ID"),
    )
    access_token = models.TextField(
        help_text=_("Meta access token used to authenticate Graph API requests."),
    )

    objects = ChatBridgeManager()

    default_site_error_message = _(
        "Default WhatsApp chat bridges cannot target a specific site."
    )

    class Meta:
        ordering = ["site__domain", "pk"]
        verbose_name = _("WhatsApp Chat Bridge")
        verbose_name_plural = _("WhatsApp Chat Bridges")
        db_table = "pages_whatsappchatbridge"
        constraints = [
            models.UniqueConstraint(
                fields=["site"],
                condition=Q(site__isnull=False),
                name="unique_whatsapp_chat_bridge_site",
            ),
            models.UniqueConstraint(
                fields=["is_default"],
                condition=Q(is_default=True),
                name="single_default_whatsapp_chat_bridge",
            ),
        ]

    def __str__(self) -> str:  # pragma: no cover - simple representation
        if self.site_id and self.site:
            return _("%(site)s → WhatsApp phone %(phone)s") % {
                "site": self.site,
                "phone": self.phone_number_id,
            }
        if self.is_default:
            return _("Default WhatsApp chat bridge (%(phone)s)") % {
                "phone": self.phone_number_id
            }
        return str(self.phone_number_id)

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if not self.phone_number_id:
            errors.setdefault("phone_number_id", []).append(
                _("Provide the WhatsApp phone number identifier used for delivery."),
            )
        if not self.access_token:
            errors.setdefault("access_token", []).append(
                _("Access token is required to authenticate with WhatsApp."),
            )
        if errors:
            raise ValidationError(errors)

    def send_message(
        self,
        *,
        recipient: str,
        content: str,
        session=None,
        message=None,
    ) -> bool:
        """Send ``content`` to ``recipient`` via WhatsApp."""

        if not self.is_enabled:
            return False
        recipient = (recipient or "").strip()
        token = (self.access_token or "").strip()
        if not recipient or not token:
            return False
        content = (content or "").strip()
        if not content:
            return False
        endpoint = f"{self.api_base_url.rstrip('/')}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "text",
            "text": {"body": content[:4096]},
        }
        timeout = getattr(settings, "PAGES_WHATSAPP_TIMEOUT", 10)
        response = None
        try:
            response = requests.post(
                endpoint, json=payload, headers=headers, timeout=timeout
            )
        except Exception:
            logger.exception(
                "Failed to send WhatsApp message %s for session %s",
                getattr(message, "pk", None),
                getattr(session, "pk", None),
            )
            return False
        try:
            if response.status_code >= 400:
                logger.warning(
                    "WhatsApp API returned %s for session %s: %s",
                    response.status_code,
                    getattr(session, "pk", None),
                    getattr(response, "text", ""),
                )
                return False
            return True
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()
