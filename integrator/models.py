from django.conf import settings
from django.db import models
from django.utils import timezone

from fernet_fields import EncryptedCharField
from utils.sigils import SigilCharField, SigilURLField


class BskyAccount(models.Model):
    """Bluesky account linked to a user."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bsky"
    )
    handle = models.CharField(max_length=255, unique=True)
    app_password = models.CharField(
        max_length=255, help_text="Bluesky app password for API access"
    )

    def __str__(self):  # pragma: no cover - simple representation
        return self.handle


class OdooInstance(models.Model):
    """Connection details for an Odoo server."""

    name = models.CharField(max_length=100)
    url = SigilURLField()
    database = SigilCharField(max_length=100)
    username = SigilCharField(max_length=100)
    password = EncryptedCharField(max_length=100)

    def __str__(self) -> str:  # pragma: no cover - simple repr
        return self.name


class RequestType(models.Model):
    """Types of requests with a unique three-letter code."""

    code = models.CharField(max_length=3, unique=True)
    name = models.CharField(max_length=100)
    next_number = models.PositiveIntegerField(default=30000)

    def __str__(self):  # pragma: no cover - simple representation
        return self.code

    def get_next_number(self):
        number = f"{self.code}{self.next_number:05d}"
        self.next_number += 1
        self.save(update_fields=["next_number"])
        return number


class Request(models.Model):
    """Request sent from one user to another requiring approval."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    request_type = models.ForeignKey(RequestType, on_delete=models.PROTECT)
    number = models.CharField(max_length=8, unique=True, editable=False)
    description = models.TextField()
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="requests_sent"
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="requests_to_approve",
    )
    status = models.CharField(
        max_length=8, choices=Status.choices, default=Status.PENDING
    )
    responded_at = models.DateTimeField(null=True, blank=True, editable=False)
    response_comment = models.TextField(blank=True)

    def __str__(self):  # pragma: no cover - simple representation
        return self.number

    def save(self, *args, **kwargs):
        if self.pk:
            old = Request.objects.get(pk=self.pk)
            if old.responded_at and (
                self.description != old.description
                or self.request_type_id != old.request_type_id
                or self.approver_id != old.approver_id
                or self.requester_id != old.requester_id
                or self.number != old.number
            ):
                raise ValueError("Cannot modify a responded request")
        else:
            if not self.number:
                self.number = self.request_type.get_next_number()
        super().save(*args, **kwargs)

    def _respond(self, status, comment=""):
        if self.status != self.Status.PENDING:
            raise ValueError("Request already responded")
        self.status = status
        self.response_comment = comment
        self.responded_at = timezone.now()
        self.save(update_fields=["status", "response_comment", "responded_at"])

    def approve(self, comment=""):
        self._respond(self.Status.APPROVED, comment)

    def reject(self, comment=""):
        self._respond(self.Status.REJECTED, comment)
