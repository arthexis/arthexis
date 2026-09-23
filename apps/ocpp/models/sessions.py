"""Charging-session and meter-value records."""

from django.db import models
from django.utils import timezone

from apps.ocpp.models.assets import Charger, Connector


class OcppTransactionQuerySet(models.QuerySet):
    """Reusable retained-transaction selections."""

    def live(self):
        """Return transactions that belong to the Arthexis-authoritative era."""
        return self.filter(historical=False)

    def historical(self):
        """Return retained pre-cutover transaction history."""
        return self.filter(historical=True)

    def active(self):
        return self.live().filter(
            recovery_state=OcppTransaction.RecoveryState.ACTIVE,
            stopped_at__isnull=True,
        )

    def unresolved(self):
        return self.live().filter(
            recovery_state=OcppTransaction.RecoveryState.UNRESOLVED,
            stopped_at__isnull=True,
        )

    def cleared(self):
        """Return sessions deliberately removed from current operational state."""
        return self.live().filter(
            recovery_state=OcppTransaction.RecoveryState.CLEARED,
            stopped_at__isnull=True,
        )

    def open(self):
        return self.live().filter(
            recovery_state__in=(
                OcppTransaction.RecoveryState.ACTIVE,
                OcppTransaction.RecoveryState.UNRESOLVED,
            ),
            stopped_at__isnull=True,
        )

    def completed(self):
        return self.filter(
            recovery_state=OcppTransaction.RecoveryState.COMPLETED,
            stopped_at__isnull=False,
        )

    def energy_unresolved(self):
        """Return completed sessions whose final energy remains unknown."""
        return self.completed().filter(energy_kwh__isnull=True)

    def energy_resolved(self):
        """Return completed sessions with a retained final energy value."""
        return self.completed().filter(energy_kwh__isnull=False)

    def recent(self):
        return self.order_by("-started_at", "-pk")


class OcppTransaction(models.Model):
    class RecoveryState(models.TextChoices):
        ACTIVE = "active", "Active"
        UNRESOLVED = "unresolved", "Unresolved"
        CLEARED = "cleared", "Operator cleared"
        COMPLETED = "completed", "Completed"

    objects = OcppTransactionQuerySet.as_manager()

    charger = models.ForeignKey(
        Charger, on_delete=models.CASCADE, related_name="transactions"
    )
    connector = models.ForeignKey(
        Connector,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    account = models.ForeignKey(
        "energy.CustomerAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ocpp_transactions",
    )
    id_tag = models.CharField(max_length=20, blank=True)
    remote_id = models.CharField(max_length=80, unique=True)
    started_at = models.DateTimeField()
    last_activity_at = models.DateTimeField(default=timezone.now)
    stopped_at = models.DateTimeField(null=True, blank=True)
    historical = models.BooleanField(default=False, db_index=True)
    recovery_state = models.CharField(
        max_length=16,
        choices=RecoveryState.choices,
        default=RecoveryState.ACTIVE,
        db_index=True,
    )
    meter_start = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    meter_stop = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    energy_kwh = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )
    recovery_cleared_at = models.DateTimeField(null=True, blank=True)
    recovery_clear_reason = models.CharField(max_length=240, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        recovery_state="completed",
                        stopped_at__isnull=False,
                    )
                    | models.Q(
                        recovery_state__in=("active", "unresolved", "cleared"),
                        stopped_at__isnull=True,
                    )
                ),
                name="ocpp_transaction_recovery_state_consistent",
            )
        ]


class MeterValue(models.Model):
    transaction = models.ForeignKey(
        OcppTransaction, on_delete=models.CASCADE, related_name="meter_values"
    )
    sampled_at = models.DateTimeField()
    value = models.DecimalField(max_digits=14, decimal_places=4)
    measurand = models.CharField(max_length=60, default="Energy.Active.Import.Register")
    unit = models.CharField(max_length=12, default="Wh")
    multiplier = models.SmallIntegerField(default=0)
    source_fingerprint = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("transaction", "source_fingerprint"),
                condition=~models.Q(source_fingerprint=""),
                name="unique_ocpp_meter_sample_fingerprint",
            )
        ]


class MeterReadingBatch(models.Model):
    """Retained standalone meter evidence that is not bound to a transaction."""

    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        related_name="meter_reading_batches",
    )
    protocol = models.CharField(max_length=12, default="ocpp2.0.1")
    evse_id = models.PositiveIntegerField(null=True, blank=True)
    reported_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("charger", "reported_at", "received_at"),
                name="ocpp_meter_batch_time_idx",
            ),
        ]
