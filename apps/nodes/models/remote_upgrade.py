from __future__ import annotations

import uuid

from django.db import models

from apps.base.models import Entity


class RemoteUpgradeRequest(Entity):
    """Audited request for a node to run its local upgrade check."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        RECEIVED = "received", "Received"
        REJECTED = "rejected", "Rejected"
        QUEUED = "queued", "Queued"
        STARTED = "started", "Started"
        COMPLETED = "completed", "Completed"

    uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    origin_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_remote_upgrade_requests",
    )
    target_node = models.ForeignKey(
        "nodes.Node",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="targeted_remote_upgrade_requests",
    )
    origin_uuid = models.UUIDField(null=True, blank=True)
    target_uuid = models.UUIDField(null=True, blank=True)
    channel = models.CharField(max_length=20, default="stable")
    options = models.JSONField(blank=True, default=dict)
    reason = models.CharField(max_length=256, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.REQUESTED,
    )
    rejection_reason = models.CharField(max_length=256, blank=True)
    trigger_result = models.CharField(max_length=128, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created"]
        verbose_name = "Remote Upgrade Request"
        verbose_name_plural = "Remote Upgrade Requests"

    def to_request_payload(self) -> dict[str, object]:
        """Return the signed payload sent to the downstream target."""

        payload: dict[str, object] = {
            "uuid": str(self.uuid),
            "origin_uuid": str(self.origin_uuid) if self.origin_uuid else "",
            "target_uuid": str(self.target_uuid) if self.target_uuid else "",
            "channel": self.channel,
            "options": dict(self.options or {}),
            "reason": self.reason,
        }
        if self.expires_at:
            payload["expires_at"] = self.expires_at.isoformat()
        return payload

    def to_response_payload(self) -> dict[str, object]:
        """Return the signed payload sent back to the upstream origin."""

        payload: dict[str, object] = {
            "uuid": str(self.uuid),
            "status": self.status,
            "channel": self.channel,
            "rejection_reason": self.rejection_reason,
            "trigger_result": self.trigger_result,
        }
        if self.responded_at:
            payload["responded_at"] = self.responded_at.isoformat()
        return payload
