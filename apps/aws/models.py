from __future__ import annotations

import json
from typing import Any

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.base.models import Entity


class AWSCredentials(Entity):
    """Stored AWS credential pair for Lightsail access."""

    name = models.CharField(max_length=150)
    access_key_id = models.CharField(max_length=128, unique=True)
    secret_access_key = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("AWS credentials")
        verbose_name_plural = _("AWS credentials")
        ordering = ("name",)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.name


class LightsailInstance(Entity):
    """Representation of an AWS Lightsail instance."""

    name = models.CharField(max_length=100)
    region = models.CharField(max_length=50)
    availability_zone = models.CharField(max_length=50, blank=True)
    state = models.CharField(max_length=50, blank=True)
    blueprint_id = models.CharField(max_length=100, blank=True)
    bundle_id = models.CharField(max_length=100, blank=True)
    public_ip = models.GenericIPAddressField(null=True, blank=True)
    private_ip = models.GenericIPAddressField(null=True, blank=True)
    arn = models.CharField(max_length=255, blank=True)
    support_code = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    username = models.CharField(max_length=100, blank=True)
    resource_type = models.CharField(max_length=100, blank=True)
    raw_details = models.JSONField(default=dict, blank=True)
    credentials = models.ForeignKey(
        AWSCredentials,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lightsail_instances",
    )

    class Meta:
        verbose_name = _("Lightsail instance")
        verbose_name_plural = _("Lightsail instances")
        unique_together = ("name", "region")
        ordering = ("name", "region")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.region})"

    @staticmethod
    def serialize_details(data: Any) -> dict[str, Any]:
        """Return JSON-serialisable details for storage."""

        try:
            return json.loads(json.dumps(data, default=str))
        except Exception:
            return {}


class LightsailDatabase(Entity):
    """Representation of an AWS Lightsail relational database."""

    name = models.CharField(max_length=100)
    region = models.CharField(max_length=50)
    availability_zone = models.CharField(max_length=50, blank=True)
    secondary_availability_zone = models.CharField(max_length=50, blank=True)
    state = models.CharField(max_length=50, blank=True)
    engine = models.CharField(max_length=50, blank=True)
    engine_version = models.CharField(max_length=50, blank=True)
    master_username = models.CharField(max_length=100, blank=True)
    backup_retention_enabled = models.BooleanField(default=False)
    publicly_accessible = models.BooleanField(default=False)
    arn = models.CharField(max_length=255, blank=True)
    endpoint_address = models.CharField(max_length=255, blank=True)
    endpoint_port = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(null=True, blank=True)
    raw_details = models.JSONField(default=dict, blank=True)
    credentials = models.ForeignKey(
        AWSCredentials,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lightsail_databases",
    )

    class Meta:
        verbose_name = _("Lightsail database")
        verbose_name_plural = _("Lightsail databases")
        unique_together = ("name", "region")
        ordering = ("name", "region")

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.name} ({self.region})"

    @staticmethod
    def serialize_details(data: Any) -> dict[str, Any]:
        """Return JSON-serialisable details for storage."""

        try:
            return json.loads(json.dumps(data, default=str))
        except Exception:
            return {}
