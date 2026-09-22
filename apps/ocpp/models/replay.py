"""Durable inbound OCPP request/replay records."""

from django.db import models

from apps.ocpp.models.assets import Charger
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.replay import ReplayPolicy


class InboundProtocolRequest(models.Model):
    """Authoritative record of one logical inbound OCPP request."""

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"

    class ResponseKind(models.TextChoices):
        RESULT = "result", "CallResult"
        ERROR = "error", "CallError"

    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="inbound_protocol_requests",
    )
    version = models.CharField(
        max_length=12,
        choices=[(version.value, version.name) for version in ProtocolVersion],
    )
    direction = models.CharField(
        max_length=24,
        choices=[(direction.value, direction.name) for direction in Direction],
        default=Direction.CHARGE_POINT_TO_CSMS.value,
    )
    action = models.CharField(max_length=80)
    unique_id = models.CharField(max_length=80)
    fingerprint = models.CharField(max_length=64)
    replay_policy = models.CharField(
        max_length=32,
        choices=[(policy.value, policy.name) for policy in ReplayPolicy],
        default=ReplayPolicy.CALL_ID_AND_FINGERPRINT.value,
    )
    domain_identity = models.CharField(max_length=160, blank=True)
    identity_key = models.CharField(max_length=64)
    request_payload = models.JSONField(default=dict)

    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PROCESSING,
    )
    response_kind = models.CharField(
        max_length=12,
        choices=ResponseKind.choices,
        blank=True,
    )
    response_payload = models.JSONField(null=True, blank=True)
    error_code = models.CharField(max_length=80, blank=True)
    error_description = models.CharField(max_length=240, blank=True)
    error_details = models.JSONField(null=True, blank=True)

    received_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("charger", "status", "received_at"),
                name="ocpp_inbound_status_idx",
            ),
            models.Index(
                fields=("charger", "unique_id", "received_at"),
                name="ocpp_inbound_call_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "charger",
                    "version",
                    "direction",
                    "action",
                    "identity_key",
                ),
                name="unique_ocpp_inbound_replay_identity",
            )
        ]
