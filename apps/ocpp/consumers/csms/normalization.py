from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import isfinite

from apps.ocpp.utils import _parse_ocpp_timestamp


class ReportChargingProfilesValidationError(ValueError):
    """Raised when a ReportChargingProfiles payload is malformed."""


@dataclass(frozen=True)
class NormalizedChargingSchedulePeriod:
    start_period: int
    limit: float
    number_phases: int | None = None
    phase_to_use: int | None = None


@dataclass(frozen=True)
class NormalizedChargingSchedule:
    charging_rate_unit: str
    periods: tuple[NormalizedChargingSchedulePeriod, ...]
    duration_seconds: int | None = None
    start_schedule: datetime | None = None
    min_charging_rate: Decimal | None = None


@dataclass(frozen=True)
class NormalizedChargingProfileReport:
    profile_id: int
    stack_level: int
    purpose: str
    kind: str
    connector_id: int
    schedule: NormalizedChargingSchedule
    recurrency_kind: str = ""
    transaction_id: int | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None


@dataclass(frozen=True)
class NormalizedChargingProfileReportPayload:
    request_id: int | None
    evse_id: int | None
    tbc: bool
    profiles: tuple[NormalizedChargingProfileReport, ...]


_INTEGER_RE = re.compile(r"^-?\d+$")


def parse_optional_int(value: object | None) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if _INTEGER_RE.fullmatch(normalized):
            try:
                return int(normalized)
            except ValueError:
                return None
    return None


def validate_report_charging_profiles_payload(
    payload: object, *, connector_value: int | None
) -> NormalizedChargingProfileReportPayload:
    payload_data = payload if isinstance(payload, dict) else {}
    request_id = parse_optional_int(payload_data.get("requestId"))
    evse_id = parse_optional_int(payload_data.get("evseId"))
    raw_tbc = payload_data.get("tbc")
    if raw_tbc is None:
        tbc = False
    elif isinstance(raw_tbc, bool):
        tbc = raw_tbc
    else:
        raise ReportChargingProfilesValidationError("tbc must be a boolean")
    raw_profiles = payload_data.get("chargingProfiles")
    if raw_profiles is None:
        raw_profiles = payload_data.get("chargingProfile")
    if isinstance(raw_profiles, dict):
        profile_entries = [raw_profiles]
    elif isinstance(raw_profiles, list):
        profile_entries = raw_profiles
    else:
        raise ReportChargingProfilesValidationError("missing chargingProfile payload")
    normalized_profiles = tuple(
        normalize_reported_charging_profile(
            entry, evse_id=evse_id, connector_value=connector_value
        )
        for entry in profile_entries
    )
    if not normalized_profiles:
        raise ReportChargingProfilesValidationError("missing chargingProfile payload")
    return NormalizedChargingProfileReportPayload(
        request_id=request_id,
        evse_id=evse_id,
        tbc=tbc,
        profiles=normalized_profiles,
    )


def normalize_reported_charging_profile(
    payload: object, *, evse_id: int | None, connector_value: int | None
) -> NormalizedChargingProfileReport:
    if not isinstance(payload, dict):
        raise ReportChargingProfilesValidationError("chargingProfile must be an object")
    raw_profile_id = payload.get("chargingProfileId")
    if raw_profile_id is None:
        raw_profile_id = payload.get("id")
    profile_id = parse_optional_int(raw_profile_id)
    if profile_id is None:
        raise ReportChargingProfilesValidationError("chargingProfileId is required")
    stack_level = parse_optional_int(payload.get("stackLevel"))
    if stack_level is None:
        raise ReportChargingProfilesValidationError("stackLevel is required")
    purpose = str(payload.get("chargingProfilePurpose") or "").strip()
    if not purpose:
        raise ReportChargingProfilesValidationError(
            "chargingProfilePurpose is required"
        )
    kind = str(payload.get("chargingProfileKind") or "").strip()
    if not kind:
        raise ReportChargingProfilesValidationError("chargingProfileKind is required")
    return NormalizedChargingProfileReport(
        profile_id=profile_id,
        stack_level=stack_level,
        purpose=purpose,
        kind=kind,
        connector_id=(
            evse_id
            if evse_id is not None
            else (connector_value if connector_value is not None else 0)
        ),
        schedule=normalize_reported_charging_schedule(payload.get("chargingSchedule")),
        recurrency_kind=str(payload.get("recurrencyKind") or "").strip(),
        transaction_id=parse_optional_int(payload.get("transactionId")),
        valid_from=_parse_ocpp_timestamp(payload.get("validFrom")),
        valid_to=_parse_ocpp_timestamp(payload.get("validTo")),
    )


def normalize_reported_charging_schedule(payload: object) -> NormalizedChargingSchedule:
    if not isinstance(payload, dict):
        raise ReportChargingProfilesValidationError("chargingSchedule is required")
    charging_rate_unit = str(payload.get("chargingRateUnit") or "").strip()
    if not charging_rate_unit:
        raise ReportChargingProfilesValidationError("chargingRateUnit is required")
    raw_periods = payload.get("chargingSchedulePeriod")
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ReportChargingProfilesValidationError(
            "chargingSchedulePeriod is required"
        )
    periods = [
        normalize_reported_charging_schedule_period(period_payload, index=index)
        for index, period_payload in enumerate(raw_periods, start=1)
    ]
    min_rate_raw = payload.get("minChargingRate")
    try:
        min_charging_rate = (
            Decimal(str(min_rate_raw)) if min_rate_raw is not None else None
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReportChargingProfilesValidationError(
            "minChargingRate is invalid"
        ) from exc
    raw_duration = payload.get("duration")
    duration_seconds = parse_optional_int(raw_duration)
    if raw_duration is not None and duration_seconds is None:
        raise ReportChargingProfilesValidationError("duration must be an integer")
    if duration_seconds is not None and duration_seconds <= 0:
        raise ReportChargingProfilesValidationError(
            "duration must be greater than zero"
        )
    if min_charging_rate is not None and not min_charging_rate.is_finite():
        raise ReportChargingProfilesValidationError("minChargingRate must be finite")
    if min_charging_rate is not None and min_charging_rate <= 0:
        raise ReportChargingProfilesValidationError(
            "minChargingRate must be greater than zero"
        )
    return NormalizedChargingSchedule(
        charging_rate_unit=charging_rate_unit,
        periods=tuple(sorted(periods, key=lambda entry: entry.start_period)),
        duration_seconds=duration_seconds,
        start_schedule=_parse_ocpp_timestamp(payload.get("startSchedule")),
        min_charging_rate=min_charging_rate,
    )


def normalize_reported_charging_schedule_period(
    payload: object, *, index: int
) -> NormalizedChargingSchedulePeriod:
    if not isinstance(payload, dict):
        raise ReportChargingProfilesValidationError(
            f"chargingSchedulePeriod[{index}] must be an object"
        )
    start_period = parse_optional_int(payload.get("startPeriod"))
    if start_period is None:
        raise ReportChargingProfilesValidationError(
            f"chargingSchedulePeriod[{index}].startPeriod is required"
        )
    try:
        limit = float(payload.get("limit"))
    except (TypeError, ValueError) as exc:
        raise ReportChargingProfilesValidationError(
            f"chargingSchedulePeriod[{index}].limit is required"
        ) from exc
    if not isfinite(limit):
        raise ReportChargingProfilesValidationError(
            f"chargingSchedulePeriod[{index}].limit must be finite"
        )
    if limit <= 0:
        raise ReportChargingProfilesValidationError(
            f"chargingSchedulePeriod[{index}].limit must be greater than zero"
        )
    return NormalizedChargingSchedulePeriod(
        start_period=start_period,
        limit=limit,
        number_phases=parse_optional_int(payload.get("numberPhases")),
        phase_to_use=parse_optional_int(payload.get("phaseToUse")),
    )
