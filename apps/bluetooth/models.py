from django.conf import settings
from django.db import models
from django.utils import timezone


class BluetoothAdapter(models.Model):
    """Represents a local Bluetooth adapter and its operational state."""

    name = models.CharField(max_length=64, unique=True, default="hci0")
    powered = models.BooleanField(default=False)
    discoverable = models.BooleanField(default=False)
    pairable = models.BooleanField(default=False)
    address = models.CharField(max_length=32, blank=True, default="")
    alias = models.CharField(max_length=255, blank=True, default="")
    last_checked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class BluetoothDevice(models.Model):
    """Stores discovered Bluetooth devices and registration state."""

    adapter = models.ForeignKey(
        BluetoothAdapter,
        related_name="devices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    address = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255, blank=True, default="")
    alias = models.CharField(max_length=255, blank=True, default="")
    icon = models.CharField(max_length=100, blank=True, default="")
    paired = models.BooleanField(default=False)
    trusted = models.BooleanField(default=False)
    blocked = models.BooleanField(default=False)
    connected = models.BooleanField(default=False)
    rssi = models.IntegerField(null=True, blank=True)
    uuids = models.JSONField(default=list, blank=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_registered = models.BooleanField(default=False)
    registered_at = models.DateTimeField(null=True, blank=True)
    registered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="registered_bluetooth_devices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ("-last_seen_at", "address")

    def __str__(self) -> str:
        return self.name or self.alias or self.address
