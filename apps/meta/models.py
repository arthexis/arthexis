from __future__ import annotations

import contextlib
import logging
import re
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from apps.chats.models import ChatBridge, ChatBridgeManager
from apps.core.entity import Entity


logger = logging.getLogger(__name__)


WHATSAPP_CHAT_BRIDGE_FEATURE_SLUG = "whatsapp-chat-bridge"
ATTENTION_KEY_RE = re.compile(r"\b(ATT-[A-F0-9]{12})\b", re.IGNORECASE)


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

    @staticmethod
    def suite_feature_slug() -> str:
        """Return the suite feature slug governing WhatsApp chat bridge traffic."""

        return WHATSAPP_CHAT_BRIDGE_FEATURE_SLUG

    @staticmethod
    def suite_feature_disable_summary() -> str:
        """Describe operator-visible disable semantics for the suite feature."""

        return gettext(
            "Disabled uses soft mode: webhook traffic is accepted for audit logging, "
            "but no WhatsApp bridge delivery or chat session message creation occurs."
        )


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
            self.route_key = secrets.token_urlsafe(18)[:48]
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

    def suite_feature_disable_summary(self) -> str:
        """Return the disable contract that operators should expect for this webhook."""

        return self.bridge.suite_feature_disable_summary()


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


class Attention(Entity):
    """Urgent attention request sent through the suite WhatsApp bridge."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RESPONDED = "responded", _("Responded")
        CANCELLED = "cancelled", _("Cancelled")

    key = models.CharField(max_length=16, unique=True, db_index=True, editable=False)
    bridge = models.ForeignKey(
        WhatsAppChatBridge,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attentions",
    )
    recipient = models.CharField(
        max_length=64,
        blank=True,
        help_text=_("WhatsApp recipient phone number for the Attention request."),
    )
    agent = models.CharField(max_length=128, blank=True)
    severity = models.CharField(max_length=32, default="urgent")
    title = models.CharField(max_length=255, default="Attention")
    message = models.TextField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    response_from_phone = models.CharField(max_length=64, blank=True)
    response_text = models.TextField(blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    response_message = models.ForeignKey(
        WhatsAppWebhookMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attentions",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = _("Attention")
        verbose_name_plural = _("Attention")

    def __str__(self) -> str:
        return f"{self.key or 'Attention'}: {self.title}"

    def save(self, *args, **kwargs):
        if not self.key:
            self.key = self._new_key()
        super().save(*args, **kwargs)

    @staticmethod
    def _new_key() -> str:
        return f"ATT-{secrets.token_hex(6).upper()}"

    @staticmethod
    def find_key(text: str) -> str:
        match = ATTENTION_KEY_RE.search(text or "")
        return match.group(1).upper() if match else ""

    @staticmethod
    def _strip_key(text: str, key: str) -> str:
        if not key:
            return (text or "").strip()
        return re.sub(re.escape(key), "", text or "", count=1, flags=re.IGNORECASE).strip(" :-\n\t")

    def notification_body(self) -> str:
        lines = [
            f"[{self.severity.upper()}] {self.title or 'Attention'}",
            f"Attention: {self.key}",
        ]
        if self.agent:
            lines.append(f"Agent: {self.agent}")
        lines.extend(
            [
                "",
                self.message.strip(),
                "",
                f"Reply with: {self.key} <answer>",
            ]
        )
        return "\n".join(lines)

    def send(self) -> bool:
        if not self.bridge_id or not self.bridge or not self.recipient:
            return False
        sent = self.bridge.send_message(
            recipient=self.recipient,
            content=self.notification_body(),
        )
        if sent:
            self.sent_at = timezone.now()
            self.save(update_fields=["sent_at", "updated_at"])
        return sent

    def mark_responded(
        self,
        *,
        response_text: str,
        response_from_phone: str = "",
        response_message: WhatsAppWebhookMessage | None = None,
        response_payload: dict | None = None,
    ) -> None:
        self.status = self.Status.RESPONDED
        self.response_text = self._strip_key(response_text, self.key)
        self.response_from_phone = response_from_phone
        self.response_message = response_message
        self.response_payload = response_payload or {}
        self.responded_at = timezone.now()
        self.save(
            update_fields=[
                "status",
                "response_text",
                "response_from_phone",
                "response_message",
                "response_payload",
                "responded_at",
                "updated_at",
            ]
        )

    @classmethod
    def capture_response(
        cls,
        *,
        text: str,
        from_phone: str = "",
        webhook_message: WhatsAppWebhookMessage | None = None,
        payload: dict | None = None,
    ) -> "Attention | None":
        key = cls.find_key(text)
        queryset = cls.objects.select_for_update().filter(status=cls.Status.PENDING)
        with transaction.atomic():
            if key:
                attention = queryset.filter(key__iexact=key).first()
            elif from_phone:
                candidates = list(queryset.filter(recipient=from_phone)[:2])
                attention = candidates[0] if len(candidates) == 1 else None
            else:
                attention = None
            if attention is None:
                return None
            attention.mark_responded(
                response_text=text,
                response_from_phone=from_phone,
                response_message=webhook_message,
                response_payload=payload,
            )
            return attention
