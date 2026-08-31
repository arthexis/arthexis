from __future__ import annotations

from .base import *
from .charging_profile import ChargingProfile

class ChargingSchedule(Entity):
    """Charging schedule linked to a :class:`ChargingProfile`."""

    profile = models.OneToOneField(
        ChargingProfile,
        on_delete=models.CASCADE,
        related_name="schedule",
    )
    start_schedule = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("Optional schedule start time; defaults to immediate execution."),
    )
    duration_seconds = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Duration (seconds)"),
    )
    charging_rate_unit = models.CharField(
        max_length=2,
        choices=ChargingProfile.RateUnit.choices,
        verbose_name=_("Charging Rate Unit"),
    )
    charging_schedule_periods = models.JSONField(
        default=list,
        blank=True,
        help_text=_(
            "List of schedule periods including start_period, limit, "
            "number_phases, and phase_to_use values."
        ),
    )
    min_charging_rate = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True,
        verbose_name=_("Minimum Charging Rate"),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Charging Schedule")
        verbose_name_plural = _("Charging Schedules")
        ordering = ["profile_id"]

    @staticmethod
    def _coerce_decimal(value: object) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _normalize_schedule_periods(self) -> tuple[list[dict[str, object]], list[str]]:
        normalized: list[dict[str, object]] = []
        errors: list[str] = []
        raw_periods = self.charging_schedule_periods or []

        for index, period in enumerate(raw_periods, start=1):
            if not isinstance(period, dict):
                errors.append(
                    _("Period %(index)s must be a mapping." % {"index": index})
                )
                continue

            start_period = period.get("start_period", period.get("startPeriod"))
            limit_value = period.get("limit")
            number_phases = period.get("number_phases", period.get("numberPhases"))
            phase_to_use = period.get("phase_to_use", period.get("phaseToUse"))

            try:
                start_int = int(start_period)
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s is missing a valid start_period."
                        % {"index": index}
                    )
                )
                continue

            decimal_limit = self._coerce_decimal(limit_value)
            if decimal_limit is None or decimal_limit <= 0:
                errors.append(
                    _(
                        "Period %(index)s is missing a positive charging limit."
                        % {"index": index}
                    )
                )
                continue

            entry: dict[str, object] = {
                "start_period": start_int,
                "limit": float(decimal_limit),
            }

            try:
                if number_phases not in {None, ""}:
                    phases_int = int(number_phases)
                    if phases_int in {1, 3}:
                        entry["number_phases"] = phases_int
                    else:
                        errors.append(
                            _(
                                "Period %(index)s number_phases must be 1 or 3."
                                % {"index": index}
                            )
                        )
                        continue
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s has an invalid number_phases value."
                        % {"index": index}
                    )
                )
                continue

            try:
                if phase_to_use not in {None, ""}:
                    phase_int = int(phase_to_use)
                    entry["phase_to_use"] = phase_int
            except (TypeError, ValueError):
                errors.append(
                    _(
                        "Period %(index)s has an invalid phase_to_use value."
                        % {"index": index}
                    )
                )
                continue

            normalized.append(entry)

        normalized.sort(key=lambda entry: entry["start_period"])
        return normalized, errors

    def clean_fields(self, exclude=None):
        exclude = exclude or []
        normalized_periods, period_errors = self._normalize_schedule_periods()
        self.charging_schedule_periods = normalized_periods

        try:
            super().clean_fields(exclude=exclude)
        except ValidationError as exc:
            errors = exc.error_dict
        else:
            errors: dict[str, list[str]] = {}

        if period_errors:
            errors.setdefault("charging_schedule_periods", []).extend(period_errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()

        errors: dict[str, list[str]] = {}

        if self.duration_seconds is not None and self.duration_seconds <= 0:
            errors.setdefault("duration_seconds", []).append(
                _("Duration must be greater than zero when provided.")
            )
        if self.min_charging_rate is not None and self.min_charging_rate <= 0:
            errors.setdefault("min_charging_rate", []).append(
                _("Minimum charging rate must be positive when provided.")
            )
        if not self.charging_schedule_periods:
            errors.setdefault("charging_schedule_periods", []).append(
                _("Provide at least one charging schedule period.")
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

    def as_charging_schedule_payload(
        self, *, periods: list[dict[str, object]] | None = None
    ) -> dict[str, object]:
        schedule: dict[str, object] = {
            "chargingRateUnit": self.charging_rate_unit,
            "chargingSchedulePeriod": [
                {
                    "startPeriod": period["start_period"],
                    "limit": period["limit"],
                    **(
                        {"numberPhases": period["number_phases"]}
                        if "number_phases" in period
                        else {}
                    ),
                    **(
                        {"phaseToUse": period["phase_to_use"]}
                        if "phase_to_use" in period
                        else {}
                    ),
                }
                for period in (periods if periods is not None else self.charging_schedule_periods)
            ],
        }

        if self.duration_seconds is not None:
            schedule["duration"] = self.duration_seconds
        if self.start_schedule:
            schedule["startSchedule"] = self._format_datetime(self.start_schedule)
        if self.min_charging_rate is not None:
            schedule["minChargingRate"] = float(self.min_charging_rate)

        return schedule
