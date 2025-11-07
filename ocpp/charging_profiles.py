"""Utilities for validating and normalizing OCPP charging profiles."""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from django.core.exceptions import ValidationError
from django.utils.dateparse import parse_datetime

ALLOWED_PURPOSES = {
    "ChargePointMaxProfile",
    "TxDefaultProfile",
    "TxProfile",
}

ALLOWED_KINDS = {
    "Absolute",
    "Recurring",
    "Relative",
}

ALLOWED_RECURRENCIES = {
    "Daily",
    "Weekly",
}

ALLOWED_RATE_UNITS = {"A", "W"}


def _clean_int(value: Any, field: str, *, minimum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValidationError({field: "Enter a whole number."}) from None
    if minimum is not None and number < minimum:
        raise ValidationError({field: "Ensure this value is greater than or equal to %(limit)s." % {"limit": minimum}})
    return number


def _clean_decimal(value: Any, field: str, *, minimum: Decimal | None = None) -> float:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError({field: "Enter a valid number."}) from None
    if minimum is not None and number < minimum:
        raise ValidationError({field: "Ensure this value is greater than or equal to %(limit)s." % {"limit": minimum}})
    return float(number)


def _clean_datetime(value: Any, field: str) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        candidate = value.strip()
    else:
        candidate = str(value)
    parsed = parse_datetime(candidate)
    if not parsed:
        raise ValidationError({field: "Enter a valid ISO 8601 datetime."})
    return parsed.isoformat()


def _normalize_schedule_period(entry: dict, index: int) -> dict:
    if not isinstance(entry, dict):
        raise ValidationError({
            "chargingSchedulePeriod": "Period %(index)d must be an object." % {"index": index + 1}
        })
    sanitized: dict[str, float | int] = {}
    sanitized["startPeriod"] = _clean_int(entry.get("startPeriod"), "startPeriod", minimum=0)
    sanitized["limit"] = _clean_decimal(entry.get("limit"), "limit", minimum=Decimal("0"))
    if "numberPhases" in entry and entry["numberPhases"] not in (None, ""):
        sanitized["numberPhases"] = _clean_int(entry.get("numberPhases"), "numberPhases", minimum=1)
    return sanitized


def _normalize_schedule(schedule: dict, index: int) -> dict:
    if not isinstance(schedule, dict):
        raise ValidationError({
            "chargingSchedule": "Schedule %(index)d must be an object." % {"index": index + 1}
        })
    sanitized: dict[str, object] = {}
    if "duration" in schedule and schedule["duration"] not in (None, ""):
        sanitized["duration"] = _clean_int(schedule.get("duration"), "duration", minimum=0)
    if "startSchedule" in schedule and schedule["startSchedule"] not in (None, ""):
        sanitized["startSchedule"] = _clean_datetime(schedule.get("startSchedule"), "startSchedule")
    unit = schedule.get("chargingRateUnit")
    if not isinstance(unit, str) or unit.strip() not in ALLOWED_RATE_UNITS:
        raise ValidationError({"chargingRateUnit": "Charging rate unit must be 'A' or 'W'."})
    sanitized["chargingRateUnit"] = unit.strip()
    periods = schedule.get("chargingSchedulePeriod")
    if not isinstance(periods, list) or not periods:
        raise ValidationError({"chargingSchedulePeriod": "Provide at least one schedule period."})
    sanitized["chargingSchedulePeriod"] = [
        _normalize_schedule_period(period, idx) for idx, period in enumerate(periods)
    ]
    if "minChargingRate" in schedule and schedule["minChargingRate"] not in (None, ""):
        sanitized["minChargingRate"] = _clean_decimal(
            schedule.get("minChargingRate"), "minChargingRate", minimum=Decimal("0")
        )
    if "salesTariff" in schedule:
        sanitized["salesTariff"] = schedule["salesTariff"]
    return sanitized


def _iter_schedules(profile: dict) -> Iterable[dict]:
    if "chargingSchedules" in profile:
        schedules = profile.get("chargingSchedules")
        if isinstance(schedules, list):
            return schedules
    schedule = profile.get("chargingSchedule")
    if isinstance(schedule, list):
        return schedule
    if isinstance(schedule, dict):
        return [schedule]
    return []


def normalize_cs_charging_profile(raw_profile: Any) -> tuple[dict, dict]:
    """Validate and sanitize a ``csChargingProfiles`` payload.

    Returns a tuple of (sanitized_profile, summary) where ``summary`` includes
    key metadata such as the profile id and stack level.
    """

    if isinstance(raw_profile, str):
        try:
            profile_obj = json.loads(raw_profile)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValidationError({"csChargingProfiles": "Upload a valid JSON document."}) from exc
    elif isinstance(raw_profile, dict):
        profile_obj = raw_profile
    else:
        raise ValidationError({"csChargingProfiles": "Provide a charging profile object."})

    profile_id = _clean_int(profile_obj.get("chargingProfileId"), "chargingProfileId", minimum=0)
    stack_level = _clean_int(profile_obj.get("stackLevel"), "stackLevel", minimum=0)

    purpose = profile_obj.get("chargingProfilePurpose")
    if not isinstance(purpose, str) or purpose.strip() not in ALLOWED_PURPOSES:
        raise ValidationError({
            "chargingProfilePurpose": "Invalid charging profile purpose."})
    purpose = purpose.strip()

    kind = profile_obj.get("chargingProfileKind")
    if not isinstance(kind, str) or kind.strip() not in ALLOWED_KINDS:
        raise ValidationError({"chargingProfileKind": "Invalid charging profile kind."})
    kind = kind.strip()

    recurrency = profile_obj.get("recurrencyKind")
    if recurrency not in (None, ""):
        if not isinstance(recurrency, str) or recurrency.strip() not in ALLOWED_RECURRENCIES:
            raise ValidationError({"recurrencyKind": "Invalid recurrency kind."})
        recurrency = recurrency.strip()
    else:
        recurrency = ""

    valid_from = ""
    if "validFrom" in profile_obj:
        valid_from = _clean_datetime(profile_obj.get("validFrom"), "validFrom")
    valid_to = ""
    if "validTo" in profile_obj:
        valid_to = _clean_datetime(profile_obj.get("validTo"), "validTo")

    transaction_id = ""
    if "transactionId" in profile_obj and profile_obj["transactionId"] not in (None, ""):
        transaction_id = str(profile_obj.get("transactionId")).strip()

    schedules = list(_iter_schedules(profile_obj))
    if not schedules:
        raise ValidationError({"chargingSchedule": "Provide at least one charging schedule."})
    sanitized_schedules = [
        _normalize_schedule(schedule, idx) for idx, schedule in enumerate(schedules)
    ]

    sanitized_profile: dict[str, object] = {
        "chargingProfileId": profile_id,
        "stackLevel": stack_level,
        "chargingProfilePurpose": purpose,
        "chargingProfileKind": kind,
        "chargingSchedules": sanitized_schedules,
    }
    if recurrency:
        sanitized_profile["recurrencyKind"] = recurrency
    if valid_from:
        sanitized_profile["validFrom"] = valid_from
    if valid_to:
        sanitized_profile["validTo"] = valid_to
    if transaction_id:
        sanitized_profile["transactionId"] = transaction_id

    if "chargingProfile" in profile_obj:
        sanitized_profile["chargingProfile"] = profile_obj["chargingProfile"]

    summary = {
        "profile_id": profile_id,
        "stack_level": stack_level,
        "purpose": purpose,
        "kind": kind,
        "recurrency": recurrency,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "transaction_id": transaction_id,
    }
    return sanitized_profile, summary


__all__ = ["normalize_cs_charging_profile"]
