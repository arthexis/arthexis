from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F
from django.utils import timezone


class SerialInterface(models.Model):
    class LinkRole(models.TextChoices):
        TARGET = "target", "Target Node"
        OPERATOR = "operator", "Operator Node"

    class InterfaceType(models.TextChoices):
        UART = "uart", "UART"
        RS485 = "rs485", "RS485"

    name = models.CharField(max_length=100, unique=True)
    device_path = models.CharField(max_length=255, unique=True)
    interface_type = models.CharField(
        max_length=20, choices=InterfaceType.choices, default=InterfaceType.UART
    )
    role = models.CharField(
        max_length=20, choices=LinkRole.choices, default=LinkRole.TARGET
    )
    baud_rate = models.PositiveIntegerField(default=115200)
    parity = models.CharField(max_length=1, default="N")
    stop_bits = models.DecimalField(max_digits=2, decimal_places=1, default=1)
    is_enabled = models.BooleanField(default=False)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.device_path})"


class SerialPeer(models.Model):
    interface = models.ForeignKey(
        SerialInterface, on_delete=models.CASCADE, related_name="peers"
    )
    node_id = models.CharField(max_length=120)
    protocol_version = models.CharField(max_length=32, default="phase1")
    shared_key_fingerprint = models.CharField(max_length=128)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("node_id",)
        constraints = [
            models.UniqueConstraint(
                fields=("interface", "node_id"),
                name="serialbridge_peer_unique_interface_node",
            ),
        ]

    def __str__(self):
        return self.node_id


class SerialSession(models.Model):
    class Status(models.TextChoices):
        CONNECTED = "connected", "Connected"
        DISCONNECTED = "disconnected", "Disconnected"

    interface = models.ForeignKey(
        SerialInterface, on_delete=models.CASCADE, related_name="sessions"
    )
    peer = models.ForeignKey(
        SerialPeer, on_delete=models.CASCADE, related_name="sessions"
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DISCONNECTED
    )
    rx_messages = models.PositiveIntegerField(default=0)
    tx_messages = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-updated_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("interface", "peer"),
                name="serialbridge_session_unique_interface_peer",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.peer_id
            and self.interface_id
            and self.peer.interface_id != self.interface_id
        ):
            raise ValidationError(
                {"peer": "Peer must belong to the selected interface."}
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def mark_ping(self):
        now = timezone.now()
        connected_at = self.connected_at or now
        type(self).objects.filter(pk=self.pk).update(
            status=self.Status.CONNECTED,
            rx_messages=F("rx_messages") + 1,
            tx_messages=F("tx_messages") + 1,
            connected_at=connected_at,
            last_seen_at=now,
            disconnected_at=None,
            last_error="",
            updated_at=now,
        )
        self.refresh_from_db(
            fields=[
                "status",
                "rx_messages",
                "tx_messages",
                "connected_at",
                "last_seen_at",
                "disconnected_at",
                "last_error",
                "updated_at",
            ]
        )


class SerialCommandAudit(models.Model):
    class CommandType(models.TextChoices):
        PING = "ping", "Health Ping"
        DIAGNOSTICS = "diagnostics", "Diagnostics Manifest"
        LOG_TAIL = "log_tail", "Tail Logs"
        RESTART = "restart", "Restart Service"
        RESTORE_NETWORK = "restore_network", "Restore Network"
        SAFE_MODE = "safe_mode", "Safe Mode"

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        ERROR = "error", "Error"

    interface = models.ForeignKey(
        SerialInterface, on_delete=models.CASCADE, related_name="command_audits"
    )
    peer = models.ForeignKey(
        SerialPeer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="command_audits",
    )
    command = models.CharField(max_length=30, choices=CommandType.choices)
    payload = models.JSONField(default=dict, blank=True)
    result = models.CharField(max_length=20, choices=Result.choices)
    result_message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
