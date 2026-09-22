import uuid

from django.db import models


class EventEnvelope(models.Model):
    class DeliveryStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHING = "dispatching", "Dispatching"
        PUBLISHED = "published", "Published"
        FAILED = "failed", "Failed"

    event_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    event_type = models.CharField(max_length=120)
    producer = models.CharField(max_length=80)
    payload = models.JSONField(default=dict)
    delivery_status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
    )
    delivery_attempts = models.PositiveIntegerField(default=0)
    last_delivery_error = models.CharField(max_length=160, blank=True)
    next_delivery_at = models.DateTimeField(null=True, blank=True)
    dispatch_started_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("delivery_status", "next_delivery_at", "created_at"),
                name="events_delivery_queue_idx",
            )
        ]
