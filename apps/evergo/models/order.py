"""Order-related models for Evergo synchronization."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import models
from django.utils import timezone

from .parsing import nested_name, to_int


class EvergoOrderFieldValue(models.Model):
    """Known dropdown-like values observed from Evergo catalogs and order payloads."""

    FIELD_SITIO = "sitio"
    FIELD_ESTATUS = "estatus"
    FIELD_INGENIERO = "ingeniero"
    FIELD_PREORDEN_TIPO = "preorden_tipo"
    FIELD_PAYMENT_BY = "payment_by"

    field_name = models.CharField(max_length=64)
    remote_id = models.PositiveIntegerField(null=True, blank=True)
    remote_name = models.CharField(max_length=255)
    local_label = models.CharField(max_length=255, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    last_seen_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Order Field Value"
        verbose_name_plural = "Evergo Order Field Values"
        unique_together = ("field_name", "remote_id")

    def __str__(self) -> str:
        """Return a concise human-readable representation."""
        return f"{self.field_name}: {self.remote_name}"


class EvergoOrder(models.Model):
    """Local cache of Evergo installer/coordinator orders for the authenticated user."""

    VALIDATION_STATE_VALIDATED = "validated"
    VALIDATION_STATE_PLACEHOLDER = "placeholder"
    VALIDATION_STATE_CHOICES = (
        (VALIDATION_STATE_VALIDATED, "Validated in Evergo"),
        (VALIDATION_STATE_PLACEHOLDER, "Temporary placeholder"),
    )

    user = models.ForeignKey("evergo.EvergoUser", on_delete=models.CASCADE, related_name="orders")
    remote_id = models.PositiveIntegerField(unique=True, db_index=True, null=True, blank=True)
    order_number = models.CharField(max_length=64, blank=True)
    prefix = models.CharField(max_length=32, blank=True)
    suffix = models.CharField(max_length=32, blank=True)
    uuid = models.PositiveIntegerField(null=True, blank=True)

    status_id = models.PositiveIntegerField(null=True, blank=True)
    status_name = models.CharField(max_length=255, blank=True)
    site_id = models.PositiveIntegerField(null=True, blank=True)
    site_name = models.CharField(max_length=255, blank=True)
    client_id = models.PositiveIntegerField(null=True, blank=True)
    client_name = models.CharField(max_length=255, blank=True)
    phone_primary = models.CharField(max_length=64, blank=True)
    phone_secondary = models.CharField(max_length=64, blank=True)

    address_street = models.CharField(max_length=255, blank=True)
    address_num_ext = models.CharField(max_length=64, blank=True)
    address_num_int = models.CharField(max_length=64, blank=True)
    address_between_streets = models.CharField(max_length=255, blank=True)
    address_neighborhood = models.CharField(max_length=255, blank=True)
    address_municipality = models.CharField(max_length=255, blank=True)
    address_city = models.CharField(max_length=255, blank=True)
    address_state = models.CharField(max_length=255, blank=True)
    address_postal_code = models.CharField(max_length=32, blank=True)

    assigned_engineer_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_engineer_name = models.CharField(max_length=255, blank=True)
    assigned_coordinator_id = models.PositiveIntegerField(null=True, blank=True)
    assigned_coordinator_name = models.CharField(max_length=255, blank=True)

    has_charger = models.BooleanField(default=False)
    has_vehicle = models.BooleanField(default=False)
    charger_count = models.PositiveIntegerField(default=0)
    estimated_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    scheduled_for = models.DateTimeField(null=True, blank=True)
    source_created_at = models.DateTimeField(null=True, blank=True)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    source_last_contact_at = models.DateTimeField(null=True, blank=True)
    raw_payload = models.JSONField(default=dict, blank=True)
    validation_state = models.CharField(
        max_length=20,
        choices=VALIDATION_STATE_CHOICES,
        default=VALIDATION_STATE_VALIDATED,
    )
    refreshed_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Evergo Order"
        verbose_name_plural = "Evergo Orders"
        ordering = ("-source_updated_at", "-remote_id")

    def __str__(self) -> str:
        """Return an informative order identifier for admin and logs."""
        if self.order_number:
            return self.order_number
        return f"EvergoOrder#{self.remote_id}" if self.remote_id is not None else "EvergoOrder#draft"

    def sync_dynamic_field_values(self, payload: dict[str, Any]) -> None:
        """Track dropdown-like field values as they appear in incoming order payloads."""
        mapping = (
            (EvergoOrderFieldValue.FIELD_SITIO, payload.get("sitio")),
            (EvergoOrderFieldValue.FIELD_ESTATUS, payload.get("estatus")),
            (EvergoOrderFieldValue.FIELD_PREORDEN_TIPO, payload.get("preorden_tipo")),
        )
        for field_name, source in mapping:
            if not isinstance(source, dict):
                continue
            remote_id = to_int(source.get("id"))
            remote_name = nested_name(source)
            if remote_id is None or not remote_name:
                continue
            EvergoOrderFieldValue.objects.update_or_create(
                field_name=field_name,
                remote_id=remote_id,
                defaults={
                    "remote_name": remote_name,
                    "raw_payload": source,
                    "last_seen_at": timezone.now(),
                },
            )

        payment_by = str(payload.get("paymentBy") or "").strip()
        if payment_by:
            EvergoOrderFieldValue.objects.update_or_create(
                field_name=EvergoOrderFieldValue.FIELD_PAYMENT_BY,
                remote_id=None,
                remote_name=payment_by,
                defaults={"raw_payload": {"value": payment_by}, "last_seen_at": timezone.now()},
            )

        amount = payload.get("monto")
        try:
            self.estimated_amount = Decimal(str(amount)) if amount not in (None, "") else None
        except (InvalidOperation, ValueError):
            self.estimated_amount = None
        self.save(update_fields=["estimated_amount", "refreshed_at"])
