from django.db import models


class Charger(models.Model):
    """Known charge point with optional configuration."""

    charger_id = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=200, blank=True)
    config = models.JSONField(default=dict, blank=True)
    require_rfid = models.BooleanField(default=False)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_meter_values = models.JSONField(default=dict, blank=True)

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return self.charger_id


class Transaction(models.Model):
    """Charging session data stored for each charger."""

    charger_id = models.CharField(max_length=100)
    transaction_id = models.BigIntegerField()
    meter_start = models.IntegerField(null=True, blank=True)
    meter_stop = models.IntegerField(null=True, blank=True)
    start_time = models.DateTimeField()
    stop_time = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ("charger_id", "transaction_id")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        return f"{self.charger_id}:{self.transaction_id}"

