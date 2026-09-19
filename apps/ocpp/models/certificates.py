"""Certificate metadata without retaining private certificate material."""

from django.db import models

from apps.ocpp.models.assets import Charger


class CertificateRecord(models.Model):
    charger = models.ForeignKey(
        Charger,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="certificate_records",
    )
    certificate_type = models.CharField(max_length=60)
    fingerprint = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=40, default="accepted")
    expires_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
