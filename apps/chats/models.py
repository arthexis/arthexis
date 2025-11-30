from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext, gettext_lazy as _

from apps.core.entity import Entity, EntityManager


logger = logging.getLogger(__name__)


class ChatBridgeManager(EntityManager):
    """Manager providing helpers for chat bridge lookups."""

    def for_site(self, site: Site | None):
        queryset = self.filter(is_enabled=True)
        if site and getattr(site, "pk", None):
            bridge = queryset.filter(site=site).first()
            if bridge:
                return bridge
        return queryset.filter(is_default=True).first()


class ChatBridge(Entity):
    """Base configuration for routing chat messages to external services."""

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_bridges",
        null=True,
        blank=True,
        help_text=_("Restrict this bridge to a specific site. Leave blank to use it as a fallback."),
    )
    is_enabled = models.BooleanField(
        default=True,
        help_text=_("Disable to stop forwarding chat messages to this bridge."),
    )
    is_default = models.BooleanField(
        default=False,
        help_text=_("Use as the fallback bridge when no site-specific configuration is defined."),
    )

    objects = ChatBridgeManager()

    default_site_error_message = _("Default chat bridges cannot target a specific site.")

    class Meta:
        abstract = True
        ordering = ["site__domain", "pk"]

    def clean(self):
        super().clean()
        errors: dict[str, list[str]] = {}
        if self.is_default and self.site_id:
            errors.setdefault("is_default", []).append(self.default_site_error_message)
        if errors:
            raise ValidationError(errors)


class ChatSession(Entity):
    """Persistent conversation between a visitor and staff."""

    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        ESCALATED = "escalated", _("Escalated")
        CLOSED = "closed", _("Closed")

    uuid = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, verbose_name=_("UUID")
    )
    site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        related_name="chat_sessions",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="chat_sessions",
        null=True,
        blank=True,
    )
    visitor_key = models.CharField(max_length=64, blank=True, db_index=True)
    whatsapp_number = models.CharField(
        max_length=64,
        blank=True,
        db_index=True,
        help_text=_("WhatsApp sender identifier associated with the chat session."),
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_activity_at = models.DateTimeField(default=timezone.now)
    last_visitor_activity_at = models.DateTimeField(null=True, blank=True)
    last_staff_activity_at = models.DateTimeField(null=True, blank=True)
    escalated_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_activity_at", "-pk"]
        db_table = "pages_chatsession"

    def save(self, *args, **kwargs):
        self.is_user_data = True
        if not self.last_activity_at:
            self.last_activity_at = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"Chat session {self.uuid}"

    def assign_user(self, user) -> None:
        if not user or not getattr(user, "is_authenticated", False):
            return
        if self.user_id == user.id:
            return
        self.user = user
        self.save(update_fields=["user"])

    def touch_activity(self, *, visitor: bool = False, staff: bool = False) -> None:
        now = timezone.now()
        updates: dict[str, object] = {"last_activity_at": now}
        if visitor:
            updates["last_visitor_activity_at"] = now
        if staff:
            updates["last_staff_activity_at"] = now
        if self.status == self.Status.CLOSED:
            updates["status"] = self.Status.OPEN
            updates["closed_at"] = None
        elif staff:
            updates["status"] = self.Status.OPEN
        for field, value in updates.items():
            setattr(self, field, value)
        update_fields = list(updates.keys())
        self.save(update_fields=update_fields)

    def close(self) -> None:
        if self.status == self.Status.CLOSED:
            return
        now = timezone.now()
        self.status = self.Status.CLOSED
        self.closed_at = now
        self.save(update_fields=["status", "closed_at"])

    def can_join(self, visitor_key: str | None, user) -> bool:
        if user and getattr(user, "is_staff", False):
            return True
        if user and getattr(user, "is_authenticated", False) and self.user_id == user.id:
            return True
        expected = (self.visitor_key or "").strip()
        provided = (visitor_key or "").strip()
        return bool(expected) and expected == provided

    def add_message(
        self,
        *,
        content: str,
        sender=None,
        from_staff: bool = False,
        display_name: str = "",
        source: str = "",
    ):
        message = ChatMessage(
            session=self,
            sender=sender if getattr(sender, "is_authenticated", False) else None,
            sender_display_name=display_name[:150],
            from_staff=from_staff,
            body=content,
        )
        message.is_user_data = True
        message.save()
        self.touch_activity(visitor=not from_staff, staff=from_staff)
        if not from_staff:
            self.notify_staff_of_message(message)
            self.maybe_escalate_on_idle()
        return message

    def maybe_escalate_on_idle(self) -> bool:
        notify = getattr(settings, "PAGES_CHAT_NOTIFY_STAFF", False)
        if not notify:
            return False
        idle_seconds = getattr(settings, "PAGES_CHAT_IDLE_ESCALATE_SECONDS", 0)
        try:
            idle_seconds = int(idle_seconds)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            idle_seconds = 0
        now = timezone.now()
        if idle_seconds <= 0:
            threshold = now
        else:
            threshold = now - timedelta(seconds=idle_seconds)
        last_staff = self.last_staff_activity_at or self.created_at
        if last_staff and last_staff >= threshold:
            return False
        if self.escalated_at and (
            last_staff is None or self.escalated_at >= last_staff
        ):
            return False
        self.escalated_at = now
        self.status = self.Status.ESCALATED
        try:
            self.save(update_fields=["escalated_at", "status"])
        except Exception:  # pragma: no cover - database failures logged
            logger.exception("Failed to record escalation for chat session %s", self.pk)
            return False
        subject = gettext("Visitor chat awaiting staff response")
        body = gettext("Chat session %(uuid)s is idle and needs staff attention.") % {
            "uuid": self.uuid
        }
        try:
            from apps.nodes.models import NetMessage

            NetMessage.broadcast(subject=subject, body=body)
        except Exception:  # pragma: no cover - propagation errors handled in logs
            logger.exception(
                "Failed to broadcast escalation NetMessage for chat session %s",
                self.pk,
            )
        return True

    def notify_staff_of_message(self, message: "ChatMessage") -> bool:
        notify = getattr(settings, "PAGES_CHAT_NOTIFY_STAFF", False)
        if not notify:
            return False

        subject = gettext("New visitor chat message")
        snippet = (message.body or "").strip()
        if snippet:
            snippet = " ".join(snippet.split())
        author = message.author_label()
        if snippet and author:
            snippet_text = gettext("%(author)s: %(snippet)s") % {
                "author": author,
                "snippet": snippet,
            }
        elif snippet:
            snippet_text = snippet
        else:
            snippet_text = gettext("Visitor sent a message without additional text.")

        if len(snippet_text) > 180:
            snippet_text = f"{snippet_text[:177]}..."

        admin_path = ""
        try:
            admin_path = reverse("admin:pages_chatsession_change", args=[self.pk])
        except Exception:
            logger.exception(
                "Failed to resolve admin URL for chat session %s", self.pk
            )

        body_parts = [
            gettext("Chat session %(uuid)s received a new visitor message.")
            % {"uuid": self.uuid}
        ]
        if snippet_text:
            body_parts.append(
                gettext("Latest message: %(snippet)s") % {"snippet": snippet_text}
            )
        if admin_path:
            body_parts.append(
                gettext("Review conversation in admin: %(path)s")
                % {"path": admin_path}
            )

        body = "\n\n".join(part for part in body_parts if part).strip()
        body = body[:256]

        try:
            from apps.nodes.models import NetMessage

            NetMessage.broadcast(subject=subject, body=body)
        except Exception:
            logger.exception(
                "Failed to broadcast chat notification for session %s", self.pk
            )
            return False
        return True


class ChatMessage(Entity):
    """Individual message stored for a chat session."""

    session = models.ForeignKey(
        ChatSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="chat_messages",
        null=True,
        blank=True,
    )
    sender_display_name = models.CharField(max_length=150, blank=True)
    from_staff = models.BooleanField(default=False)
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        db_table = "pages_chatmessage"

    def save(self, *args, **kwargs):
        self.is_user_data = True
        super().save(*args, **kwargs)

    def author_label(self) -> str:
        if self.sender_display_name:
            return self.sender_display_name
        if self.sender_id and self.sender:
            return getattr(self.sender, "get_full_name", lambda: "")() or getattr(
                self.sender, "username", ""
            )
        return gettext("Visitor") if not self.from_staff else gettext("Staff")

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.pk,
            "content": self.body,
            "created": self.created_at.isoformat(),
            "from_staff": self.from_staff,
            "author": self.author_label(),
        }
