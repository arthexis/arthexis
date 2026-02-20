from __future__ import annotations

import contextlib
import logging
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.translation import gettext, gettext_lazy as _

from apps.chats.models import ChatBridge, ChatBridgeManager
from apps.core.entity import Entity


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
                    "WhatsApp API returned %s (%s) for session %s",
                    response.status_code,
                    response.reason,
                    getattr(session, "pk", None),
                )
                return False
            return True
        finally:
            if response is not None:
                close = getattr(response, "close", None)
                if callable(close):
                    with contextlib.suppress(Exception):
                        close()


class WhatsAppWebhook(Entity):
    """Webhook endpoint configuration for inbound WhatsApp events."""

    bridge = models.OneToOneField(
        WhatsAppChatBridge,
        on_delete=models.CASCADE,
        related_name="webhook",
        help_text=_("Bridge that owns this inbound webhook endpoint."),
    )
    route_key = models.SlugField(
        max_length=48,
        unique=True,
        default="",
        help_text=_("Unique key used in the webhook URL path for routing."),
    )
    verify_token = models.CharField(
        max_length=96,
        default="",
        help_text=_("Verification token copied into the Meta webhook setup page."),
    )

    class Meta:
        ordering = ["bridge__site__domain", "pk"]
        verbose_name = _("WhatsApp Webhook")
        verbose_name_plural = _("WhatsApp Webhooks")

    def __str__(self) -> str:
        """Return an admin-friendly identifier for the webhook."""

        return _("Webhook for %(bridge)s") % {"bridge": self.bridge}

    def save(self, *args, **kwargs):
        """Persist webhook defaults when credentials were not explicitly set."""

        if not self.route_key:
            self.route_key = secrets.token_urlsafe(18).replace("-", "a")[:48]
        if not self.verify_token:
            self.verify_token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def webhook_path(self) -> str:
        """Return the relative URL used by Meta to deliver webhook requests."""

        return reverse("meta:whatsapp-webhook", kwargs={"route_key": self.route_key})

    def webhook_url(self) -> str:
        """Return the full URL that should be configured in Meta."""

        site = self.bridge.site or Site.objects.get_current()
        return f"https://{site.domain}{self.webhook_path()}"

    def verify_querystring(self) -> str:
        """Return helper query parameters expected by webhook verification requests."""

        return urlencode({"hub.mode": "subscribe", "hub.verify_token": self.verify_token})


class WhatsAppWebhookMessage(Entity):
    """Inbound WhatsApp message persisted from webhook payloads."""

    webhook = models.ForeignKey(
        WhatsAppWebhook,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    message_id = models.CharField(max_length=96, db_index=True)
    messaging_product = models.CharField(max_length=32, blank=True)
    from_phone = models.CharField(max_length=32, blank=True)
    wa_id = models.CharField(max_length=32, blank=True)
    profile_name = models.CharField(max_length=255, blank=True)
    timestamp = models.BigIntegerField(null=True, blank=True)
    message_type = models.CharField(max_length=32, blank=True)
    text_body = models.TextField(blank=True)
    context_message_id = models.CharField(max_length=96, blank=True)
    metadata_phone_number_id = models.CharField(max_length=96, blank=True)
    metadata_display_phone_number = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-pk"]
        verbose_name = _("WhatsApp Webhook Message")
        verbose_name_plural = _("WhatsApp Webhook Messages")
        constraints = [
            models.UniqueConstraint(
                fields=["webhook", "message_id"],
                name="meta_unique_webhook_message_id",
            )
        ]

    def __str__(self) -> str:
        """Return a concise text for changelists and logs."""

        return _("%(sender)s → %(type)s") % {
            "sender": self.from_phone or self.wa_id or gettext("unknown"),
            "type": self.message_type or gettext("message"),
        }
