from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
import json
import logging
import uuid

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.serializers.base import DeserializationError
from django.db import models
from django.utils import timezone

from apps.base.models import Entity
from apps.core.notifications import LcdChannel
from apps.sigils.fields import SigilShortAutoField

from .features import NodeFeature
from .node import Node
from .role import NodeRole

logger = logging.getLogger(__name__)

class NetMessage(Entity):
    """Message propagated across nodes."""

    uuid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        verbose_name="UUID",
    )
    node_origin = models.ForeignKey(
        "Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_net_messages",
    )
    subject = models.CharField(max_length=64, blank=True)
    body = models.CharField(max_length=256, blank=True)
    attachments = models.JSONField(blank=True, null=True)
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="UTC timestamp after which this message should be discarded.",
    )
    lcd_channel_type = models.CharField(
        max_length=20,
        blank=True,
        default=LcdChannel.LOW.value,
        help_text="LCD channel type for local display (for example low, high, clock, or uptime).",
    )
    lcd_channel_num = models.PositiveSmallIntegerField(
        default=0,
        help_text="LCD channel number to target when displaying locally.",
    )
    filter_node = models.ForeignKey(
        "Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="filtered_net_messages",
        verbose_name="Node",
    )
    filter_node_feature = models.ForeignKey(
        "NodeFeature",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Node feature",
    )
    filter_node_role = models.ForeignKey(
        NodeRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="filtered_net_messages",
        verbose_name="Node role",
    )
    filter_current_relation = models.CharField(
        max_length=10,
        blank=True,
        choices=Node.Relation.choices,
        verbose_name="Current relation",
    )
    filter_installed_version = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="Installed version",
    )
    filter_installed_revision = models.CharField(
        max_length=40,
        blank=True,
        verbose_name="Installed revision",
    )
    reach = models.ForeignKey(
        NodeRole,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    target_limit = models.PositiveSmallIntegerField(
        default=6,
        blank=True,
        null=True,
        help_text="Maximum number of peers to contact when propagating.",
    )
    propagated_to = models.ManyToManyField(
        Node, blank=True, related_name="received_net_messages"
    )
    created = models.DateTimeField(auto_now_add=True)
    complete = models.BooleanField(default=False, editable=False)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Net Message"
        verbose_name_plural = "Net Messages"

    @classmethod
    def broadcast(
        cls,
        subject: str,
        body: str,
        reach: NodeRole | str | None = None,
        seen: list[str] | None = None,
        attachments: list[dict[str, object]] | None = None,
        expires_at: datetime | str | None = None,
        lcd_channel_type: str | None = None,
        lcd_channel_num: int | None = None,
    ):
        """Create and propagate a network message."""
        role = None
        if reach:
            if isinstance(reach, NodeRole):
                role = reach
            else:
                role = NodeRole.objects.filter(name=reach).first()
        else:
            role = NodeRole.objects.filter(name="Terminal").first()
        origin = Node.get_local()
        normalized_channel_type, normalized_channel_num = cls.normalize_lcd_channel(
            lcd_channel_type, lcd_channel_num
        )
        normalized_attachments = cls.normalize_attachments(attachments)
        msg = cls.objects.create(
            subject=subject[:64],
            body=body[:256],
            reach=role,
            node_origin=origin,
            attachments=normalized_attachments or None,
            expires_at=cls.normalize_expires_at(expires_at),
            lcd_channel_type=normalized_channel_type,
            lcd_channel_num=normalized_channel_num,
        )
        if normalized_attachments:
            msg.apply_attachments(normalized_attachments)
        msg.notify_slack()
        msg.propagate(seen=seen or [])
        return msg

    def notify_slack(self):
        """Send this Net Message to any Slack chatbots owned by the origin node."""

        try:
            SlackBotProfile = apps.get_model("teams", "SlackBotProfile")
        except (LookupError, ValueError):
            return
        if SlackBotProfile is None:
            return

        origin = self.node_origin
        if origin is None:
            origin = Node.get_local()
        if not origin:
            return

        try:
            bots = SlackBotProfile.objects.filter(node=origin, is_enabled=True)
        except Exception:  # pragma: no cover - database errors surfaced in logs
            logger.exception(
                "Failed to load Slack chatbots for node %s", getattr(origin, "pk", None)
            )
            return

        for bot in bots:
            try:
                bot.broadcast_net_message(self)
            except Exception:  # pragma: no cover - network errors logged for diagnosis
                logger.exception(
                    "Slack bot %s failed to broadcast NetMessage %s",
                    getattr(bot, "pk", None),
                    getattr(self, "pk", None),
                )

    @staticmethod
    def normalize_attachments(
        attachments: object,
    ) -> list[dict[str, object]]:
        """Normalize raw attachment payloads into serialized objects."""
        if not attachments or not isinstance(attachments, list):
            return []
        normalized: list[dict[str, object]] = []
        for item in attachments:
            if not isinstance(item, dict):
                continue
            model_label = item.get("model")
            fields = item.get("fields")
            if not isinstance(model_label, str) or not isinstance(fields, dict):
                continue
            normalized_item: dict[str, object] = {
                "model": model_label,
                "fields": deepcopy(fields),
            }
            if "pk" in item:
                normalized_item["pk"] = item["pk"]
            normalized.append(normalized_item)
        return normalized

    @staticmethod
    def normalize_expires_at(value: datetime | str | None) -> datetime | None:
        """Parse and normalize an expiration timestamp."""
        if not value:
            return None

        parsed: datetime | None
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError:
                return None

        if timezone.is_naive(parsed):
            try:
                parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
            except Exception:
                return None

        return parsed

    @staticmethod
    def normalize_lcd_channel(
        channel_type: object | None, channel_num: object | None
    ) -> tuple[str, int]:
        """Normalize LCD channel metadata."""
        normalized_type = (
            str(channel_type or LcdChannel.LOW.value).strip() or LcdChannel.LOW.value
        ).lower()
        try:
            normalized_num = int(channel_num) if channel_num is not None else 0
        except (TypeError, ValueError):
            normalized_num = 0
        if normalized_num < 0:
            normalized_num = 0
        return normalized_type[:20], normalized_num

    @property
    def is_expired(self) -> bool:
        """Return ``True`` when the message has expired."""
        if not self.expires_at:
            return False
        return self.expires_at <= timezone.now()

    def apply_attachments(
        self, attachments: list[dict[str, object]] | None = None
    ) -> None:
        """Persist attachment objects included with the message."""
        payload = attachments if attachments is not None else self.attachments or []
        if not payload:
            return

        try:
            objects = list(
                serializers.deserialize(
                    "python", deepcopy(payload), ignorenonexistent=True
                )
            )
        except DeserializationError:
            logger.exception("Failed to deserialize attachments for NetMessage %s", self.pk)
            return
        for obj in objects:
            try:
                obj.save()
            except Exception:
                logger.exception(
                    "Failed to save attachment %s for NetMessage %s",
                    getattr(obj, "object", obj),
                    self.pk,
                )

    def _build_payload(
        self,
        *,
        sender_id: str | None,
        origin_uuid: str | None,
        reach_name: str | None,
        seen: list[str],
    ) -> dict[str, object]:
        """Return the payload sent to peer nodes."""
        from apps.sigils.sigil_resolver import resolve_sigils

        payload: dict[str, object] = {
            "uuid": str(self.uuid),
            "subject": resolve_sigils(self.subject or "", current=self.node_origin),
            "body": resolve_sigils(self.body or "", current=self.node_origin),
            "seen": list(seen),
            "reach": reach_name,
            "sender": sender_id,
            "origin": origin_uuid,
        }
        channel_type, channel_num = self.normalize_lcd_channel(
            self.lcd_channel_type, self.lcd_channel_num
        )
        payload["lcd_channel_type"] = channel_type
        payload["lcd_channel_num"] = channel_num
        if self.attachments:
            payload["attachments"] = self.attachments
        if self.expires_at:
            payload["expires_at"] = self.expires_at.isoformat()
        if self.filter_node:
            payload["filter_node"] = str(self.filter_node.uuid)
        if self.filter_node_feature:
            payload["filter_node_feature"] = self.filter_node_feature.slug
        if self.filter_node_role:
            payload["filter_node_role"] = self.filter_node_role.name
        if self.filter_current_relation:
            payload["filter_current_relation"] = self.filter_current_relation
        if self.filter_installed_version:
            payload["filter_installed_version"] = self.filter_installed_version
        if self.filter_installed_revision:
            payload["filter_installed_revision"] = self.filter_installed_revision
        return payload

    @staticmethod
    def _serialize_payload(payload: dict[str, object]) -> str:
        """Serialize a payload into deterministic JSON."""
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _sign_payload(payload_json: str, private_key) -> str | None:
        """Return the signature for a payload when possible."""
        signature, _error = Node.sign_payload(payload_json, private_key)
        return signature

    def queue_for_node(self, node: "Node", seen: list[str]) -> None:
        """Queue this message for later delivery to ``node``."""

        if node.current_relation != Node.Relation.DOWNSTREAM:
            return

        if self.is_expired:
            if not self.complete:
                self.complete = True
                if self.pk:
                    self.save(update_fields=["complete"])
            self.clear_queue_for_node(node)
            return

        now = timezone.now()
        expires_at = now + timedelta(hours=1)
        if self.expires_at:
            expires_at = min(expires_at, self.expires_at)
        normalized_seen = [str(value) for value in seen]
        entry, created = PendingNetMessage.objects.get_or_create(
            node=node,
            message=self,
            defaults={
                "seen": normalized_seen,
                "stale_at": expires_at,
            },
        )
        if created:
            entry.queued_at = now
            entry.save(update_fields=["queued_at"])
        else:
            entry.seen = normalized_seen
            entry.stale_at = expires_at
            entry.queued_at = now
            entry.save(update_fields=["seen", "stale_at", "queued_at"])
        self._trim_queue(node)

    def clear_queue_for_node(self, node: "Node") -> None:
        """Remove queued deliveries for ``node``."""
        PendingNetMessage.objects.filter(node=node, message=self).delete()

    def _trim_queue(self, node: "Node") -> None:
        """Trim the queued messages for a node to its configured limit."""
        limit = max(int(node.message_queue_length or 0), 0)
        if limit == 0:
            PendingNetMessage.objects.filter(node=node).delete()
            return
        qs = PendingNetMessage.objects.filter(node=node).order_by("-queued_at")
        keep_ids = list(qs.values_list("pk", flat=True)[:limit])
        if keep_ids:
            PendingNetMessage.objects.filter(node=node).exclude(pk__in=keep_ids).delete()
        else:
            qs.delete()

    @classmethod
    def receive_payload(
        cls,
        data: dict[str, object],
        *,
        sender: "Node",
    ) -> "NetMessage":
        """Create or update a message from an inbound payload."""
        from apps.nodes.services.propagation import receive_payload

        return receive_payload(cls, data, sender=sender)

    def propagate(self, seen: list[str] | None = None):
        """Propagate the message to eligible peer nodes."""
        from apps.nodes.services.propagation import propagate

        propagate(self, seen=seen)


class PendingNetMessage(Entity):
    """Queued :class:`NetMessage` awaiting delivery to a downstream node."""

    node = models.ForeignKey(
        Node, on_delete=models.CASCADE, related_name="pending_net_messages"
    )
    message = models.ForeignKey(
        NetMessage,
        on_delete=models.CASCADE,
        related_name="pending_deliveries",
    )
    seen = models.JSONField(default=list)
    queued_at = models.DateTimeField(auto_now_add=True)
    stale_at = models.DateTimeField()

    class Meta:
        unique_together = ("node", "message")
        ordering = ("queued_at",)
        verbose_name = "Pending Net Message"
        verbose_name_plural = "Pending Net Messages"

    def __str__(self) -> str:  # pragma: no cover - simple representation
        """Return a concise label for the pending message."""
        return f"{self.message_id} → {self.node_id}"

    @property
    def is_stale(self) -> bool:
        """Return ``True`` when the pending message is stale."""
        if self.message and getattr(self.message, "is_expired", False):
            return True
        return self.stale_at <= timezone.now()

