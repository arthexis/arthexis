"""Charging-session and meter-value records."""

from django.db import models

from apps.ocpp.models.assets import Charger, Connector


class OcppTransactionQuerySet(models.QuerySet):
    """Reusable retained-transaction selections."""

    def active(self):
        return self.filter(stopped_at__isnull=True)

    def completed(self):
        return self.filter(stopped_at__isnull=False)

    def recent(self):
        return self.order_by("-started_at", "-pk")


class OcppTransaction(models.Model):
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
    stopped_at = models.DateTimeField(null=True, blank=True)
    meter_start = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    meter_stop = models.DecimalField(max_digits=14, decimal_places=4, null=True)
    energy_kwh = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        null=True,
        blank=True,
    )


class MeterValue(models.Model):
    transaction = models.ForeignKey(
        OcppTransaction, on_delete=models.CASCADE, related_name="meter_values"
    )
    sampled_at = models.DateTimeField()
    value = models.DecimalField(max_digits=14, decimal_places=4)
    measurand = models.CharField(max_length=60, default="Energy.Active.Import.Register")
    unit = models.CharField(max_length=12, default="Wh")
    multiplier = models.SmallIntegerField(default=0)
