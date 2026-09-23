"""Persisted outbound OCPP operation and correlation outcomes."""

import uuid

from django.db import models

from apps.ocpp.models.assets import Charger


class ProtocolOperation(models.Model):
    class Direction(models.TextChoices):
        CHARGE_POINT_TO_CSMS = "charge_point_to_csms", "Charge point to CSMS"
        CSMS_TO_CHARGE_POINT = "csms_to_charge_point", "CSMS to charge point"

    class RecoveryPolicy(models.TextChoices):
        SAFE_RETRY = "safe_retry", "Safe retry"
        RECONCILE = "reconcile", "Reconcile"
        MANUAL = "manual", "Manual"

    class ReconciliationResolution(models.TextChoices):
        ACHIEVED = "achieved", "Desired state achieved"
        NOT_ACHIEVED = "not_achieved", "Desired state not achieved"
        IRRELEVANT = "irrelevant", "No longer operationally relevant"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DELIVERING = "delivering", "Delivering"
        RECOVERY_REQUIRED = "recovery_required", "Recovery required"
        COMPLETED = "completed", "Completed"
        ERRORED = "errored", "Errored"
        TIMED_OUT = "timed_out", "Timed out"
        DISCONNECTED = "disconnected", "Disconnected"

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="protocol_operations"
    )
    version = models.CharField(max_length=12)
    direction = models.CharField(max_length=24, choices=Direction.choices)
    action = models.CharField(max_length=80)
    unique_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    request_payload = models.JSONField(default=dict)
    response_payload = models.JSONField(null=True, blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.PENDING
    )
    recovery_policy = models.CharField(
        max_length=16,
        choices=RecoveryPolicy.choices,
        default=RecoveryPolicy.MANUAL,
    )
    error_code = models.CharField(max_length=80, blank=True)
    error_description = models.CharField(max_length=240, blank=True)
    last_delivery_error = models.CharField(max_length=240, blank=True)
    delivery_owner = models.CharField(max_length=255, blank=True)
    attempt_token = models.UUIDField(null=True, blank=True, editable=False)
    attempt_count = models.PositiveIntegerField(default=0)
    first_attempt_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reconciliation_checked_at = models.DateTimeField(null=True, blank=True)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    reconciliation_resolution = models.CharField(
        max_length=24,
        choices=ReconciliationResolution.choices,
        blank=True,
    )
    reconciliation_basis = models.CharField(max_length=240, blank=True)

    class Meta:
        indexes = [models.Index(fields=("charger", "status", "created_at"))]
