from __future__ import annotations

from .base import *

class ChargingProfile(Entity):
    """Charging profiles dispatched through SetChargingProfile."""

    class Purpose(models.TextChoices):
        CHARGE_POINT_MAX_PROFILE = "ChargePointMaxProfile", _(
            "Charge Point Max Profile"
        )
        TX_DEFAULT_PROFILE = "TxDefaultProfile", _("Transaction Default Profile")
        TX_PROFILE = "TxProfile", _("Transaction Profile")

    class Kind(models.TextChoices):
        ABSOLUTE = "Absolute", _("Absolute")
        RECURRING = "Recurring", _("Recurring")
        RELATIVE = "Relative", _("Relative")

    class RecurrencyKind(models.TextChoices):
        DAILY = "Daily", _("Daily")
        WEEKLY = "Weekly", _("Weekly")

    class RateUnit(models.TextChoices):
        AMP = "A", _("Amperes")
        WATT = "W", _("Watts")

    charger = models.ForeignKey(
        "Charger",
        on_delete=models.CASCADE,
        related_name="charging_profiles",
        verbose_name=_("Charger"),
        null=True,
        blank=True,
        help_text=_("Optional default charger context for the profile."),
    )
    connector_id = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Connector ID"),
        help_text=_(
            "Connector targeted by this profile (0 sends the profile to the EVCS)."
        ),
    )
    charging_profile_id = models.PositiveIntegerField(
        verbose_name=_("Charging Profile ID"),
        help_text=_("Identifier sent as chargingProfileId in OCPP payloads."),
    )
    stack_level = models.PositiveIntegerField(
        default=0,
        verbose_name=_("Stack Level"),
        help_text=_("Priority applied when multiple profiles overlap."),
    )
    purpose = models.CharField(
        max_length=32,
        choices=Purpose.choices,
        verbose_name=_("Purpose"),
    )
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        verbose_name=_("Profile Kind"),
    )
    recurrency_kind = models.CharField(
        max_length=16,
        choices=RecurrencyKind.choices,
        blank=True,
        default="",
        verbose_name=_("Recurrency Kind"),
    )
    transaction_id = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Transaction ID"),
        help_text=_("Required when Purpose is TxProfile."),
    )
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True, default="")
    last_status = models.CharField(max_length=32, blank=True, default="")
    last_status_info = models.CharField(max_length=255, blank=True, default="")
    last_response_payload = models.JSONField(default=dict, blank=True)
    last_response_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = [
            "charger__charger_id",
            "connector_id",
            "-stack_level",
            "charging_profile_id",
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["charger", "connector_id", "charging_profile_id"],
                name="unique_charging_profile_per_connector",
                condition=Q(charger__isnull=False),
            )
        ]
        verbose_name = _("Charging Profile")
        verbose_name_plural = _("Charging Profiles")

    def __str__(self) -> str:  # pragma: no cover - simple representation
        connector = self.connector_id or 0
        return (
            f"{self.charger or 'Unassigned'} | {connector} | "
            f"{self.charging_profile_id}"
        )

    def clean(self):
        super().clean()

        errors: dict[str, list[str]] = {}

        if self.recurrency_kind and self.kind != self.Kind.RECURRING:
            errors.setdefault("recurrency_kind", []).append(
                _("Recurrency kind is only valid for recurring profiles.")
            )
        if self.kind == self.Kind.RECURRING and not self.recurrency_kind:
            errors.setdefault("recurrency_kind", []).append(
                _("Recurring profiles must define a recurrency kind.")
            )
        if self.purpose == self.Purpose.TX_PROFILE and not self.transaction_id:
            errors.setdefault("transaction_id", []).append(
                _("Transaction ID is required for TxProfile entries.")
            )

        if self.pk and not getattr(self, "schedule", None):
            errors.setdefault("schedule", []).append(
                _("Each charging profile must include a schedule.")
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def _format_datetime(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        if timezone.is_naive(value):
            return value.isoformat()
        return timezone.localtime(value).isoformat()

    def _schedule_payload(self) -> dict[str, object]:
        if not getattr(self, "schedule", None):
            return {}
        return self.schedule.as_charging_schedule_payload()

    def as_cs_charging_profile(
        self, *, schedule_payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "chargingProfileId": self.charging_profile_id,
            "stackLevel": self.stack_level,
            "chargingProfilePurpose": self.purpose,
            "chargingProfileKind": self.kind,
            "chargingSchedule": schedule_payload or self._schedule_payload(),
        }

        if self.transaction_id:
            payload["transactionId"] = self.transaction_id
        if self.recurrency_kind:
            payload["recurrencyKind"] = self.recurrency_kind
        if self.valid_from:
            payload["validFrom"] = self._format_datetime(self.valid_from)
        if self.valid_to:
            payload["validTo"] = self._format_datetime(self.valid_to)

        return payload

    def as_set_charging_profile_request(
        self,
        *,
        connector_id: int | None = None,
        schedule_payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        connector_value = self.connector_id if connector_id is None else connector_id
        return {
            "connectorId": connector_value,
            "csChargingProfiles": self.as_cs_charging_profile(
                schedule_payload=schedule_payload
            ),
        }

    def matches_clear_filter(
        self,
        *,
        profile_id: int | None = None,
        connector_id: int | None = None,
        purpose: str | None = None,
        stack_level: int | None = None,
    ) -> bool:
        if profile_id is not None and self.charging_profile_id != profile_id:
            return False
        if connector_id is not None and self.connector_id != connector_id:
            return False
        if purpose is not None and self.purpose != purpose:
            return False
        if stack_level is not None and self.stack_level != stack_level:
            return False
        return True
