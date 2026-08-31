"""Charging profile report handlers for CSMS consumers."""

from __future__ import annotations

from channels.db import database_sync_to_async
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.ocpp import store
from apps.ocpp.consumers.csms.normalization import (
    NormalizedChargingProfileReport,
    NormalizedChargingProfileReportPayload,
    NormalizedChargingSchedule,
    NormalizedChargingSchedulePeriod,
    ReportChargingProfilesValidationError,
    parse_optional_int,
    validate_report_charging_profiles_payload,
)
from apps.ocpp.models import Charger, ChargingProfile, ChargingSchedule
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel


class ChargingProfileHandlersMixin:
    """Handle OCPP charging profile reports and reconciliation."""

    @staticmethod
    def _parse_optional_int(value: object | None) -> int | None:
        """Return an integer value when coercion succeeds."""

        return parse_optional_int(value)

    def _validate_report_charging_profiles_payload(
        self, payload: object
    ) -> NormalizedChargingProfileReportPayload:
        """Validate and normalize a ReportChargingProfiles payload."""

        return validate_report_charging_profiles_payload(
            payload,
            connector_value=self.connector_value,
        )

    def _normalize_reported_charging_profile(
        self, payload: object, *, evse_id: int | None
    ) -> NormalizedChargingProfileReport:
        return validate_report_charging_profiles_payload(
            {"evseId": evse_id, "chargingProfile": payload},
            connector_value=self.connector_value,
        ).profiles[0]

    @staticmethod
    def _normalized_schedule_payload(
        schedule: NormalizedChargingSchedule,
    ) -> dict[str, object]:
        """Serialize a normalized schedule for logging and comparisons."""

        payload: dict[str, object] = {
            "chargingRateUnit": schedule.charging_rate_unit,
            "periods": [
                {
                    "startPeriod": period.start_period,
                    "limit": period.limit,
                    **(
                        {"numberPhases": period.number_phases}
                        if period.number_phases is not None
                        else {}
                    ),
                    **(
                        {"phaseToUse": period.phase_to_use}
                        if period.phase_to_use is not None
                        else {}
                    ),
                }
                for period in schedule.periods
            ],
        }
        if schedule.duration_seconds is not None:
            payload["duration"] = schedule.duration_seconds
        if schedule.start_schedule is not None:
            payload["startSchedule"] = schedule.start_schedule.isoformat()
        if schedule.min_charging_rate is not None:
            payload["minChargingRate"] = float(schedule.min_charging_rate)
        return payload

    @staticmethod
    def _model_schedule_to_normalized(
        schedule: ChargingSchedule,
    ) -> NormalizedChargingSchedule:
        """Convert a trusted schedule model into the normalized representation."""

        return NormalizedChargingSchedule(
            charging_rate_unit=schedule.charging_rate_unit,
            periods=tuple(
                NormalizedChargingSchedulePeriod(
                    start_period=int(period["start_period"]),
                    limit=float(period["limit"]),
                    number_phases=(
                        int(period["number_phases"])
                        if period.get("number_phases") is not None
                        else None
                    ),
                    phase_to_use=(
                        int(period["phase_to_use"])
                        if period.get("phase_to_use") is not None
                        else None
                    ),
                )
                for period in schedule.charging_schedule_periods
            ),
            duration_seconds=schedule.duration_seconds,
            start_schedule=schedule.start_schedule,
            min_charging_rate=schedule.min_charging_rate,
        )

    def _model_profile_to_normalized(
        self, profile: ChargingProfile
    ) -> NormalizedChargingProfileReport:
        """Convert a trusted charging profile model into the normalized representation."""

        return NormalizedChargingProfileReport(
            profile_id=profile.charging_profile_id,
            stack_level=profile.stack_level,
            purpose=profile.purpose,
            kind=profile.kind,
            connector_id=profile.connector_id,
            schedule=self._model_schedule_to_normalized(profile.schedule),
            recurrency_kind=profile.recurrency_kind,
            transaction_id=profile.transaction_id,
            valid_from=profile.valid_from,
            valid_to=profile.valid_to,
        )

    def _resolve_report_charging_profile_charger(
        self, report: NormalizedChargingProfileReportPayload
    ) -> Charger | None:
        """Resolve the charger row that should receive a reported profile payload."""

        connector_id = (
            report.evse_id
            if report.evse_id is not None
            else (self.connector_value if self.connector_value is not None else 0)
        )
        charger = self.charger
        if (
            charger is not None
            and self.charger_id
            and charger.charger_id == self.charger_id
            and charger.connector_id == connector_id
        ):
            return charger
        if self.charger_id:
            charger, _created = Charger.objects.get_or_create(
                charger_id=self.charger_id,
                connector_id=connector_id,
            )
            return charger
        return charger

    def _normalized_profile_payload(
        self, profile: NormalizedChargingProfileReport
    ) -> dict[str, object]:
        """Serialize a normalized profile for logging and comparisons."""

        payload: dict[str, object] = {
            "id": profile.profile_id,
            "stackLevel": profile.stack_level,
            "purpose": profile.purpose,
            "kind": profile.kind,
            "schedule": self._normalized_schedule_payload(profile.schedule),
        }
        if profile.recurrency_kind:
            payload["recurrencyKind"] = profile.recurrency_kind
        if profile.transaction_id is not None:
            payload["transactionId"] = profile.transaction_id
        if profile.valid_from is not None:
            payload["validFrom"] = profile.valid_from.isoformat()
        if profile.valid_to is not None:
            payload["validTo"] = profile.valid_to.isoformat()
        return payload

    def _persist_reported_charging_profiles(
        self,
        report: NormalizedChargingProfileReportPayload,
        *,
        charger: Charger | None = None,
    ) -> Charger | None:
        """Persist reported charging profiles for the charger."""

        charger = charger or self._resolve_report_charging_profile_charger(report)
        if charger is None:
            return None

        with transaction.atomic():
            for profile in report.profiles:
                profile_obj, _created = ChargingProfile.objects.update_or_create(
                    charger=charger,
                    connector_id=profile.connector_id,
                    charging_profile_id=profile.profile_id,
                    defaults={
                        "stack_level": profile.stack_level,
                        "purpose": profile.purpose,
                        "kind": profile.kind,
                        "recurrency_kind": profile.recurrency_kind,
                        "transaction_id": profile.transaction_id,
                        "valid_from": profile.valid_from,
                        "valid_to": profile.valid_to,
                    },
                )
                ChargingSchedule.objects.update_or_create(
                    profile=profile_obj,
                    defaults={
                        "charging_rate_unit": profile.schedule.charging_rate_unit,
                        "duration_seconds": profile.schedule.duration_seconds,
                        "start_schedule": profile.schedule.start_schedule,
                        "charging_schedule_periods": [
                            {
                                "start_period": period.start_period,
                                "limit": period.limit,
                                **(
                                    {"number_phases": period.number_phases}
                                    if period.number_phases is not None
                                    else {}
                                ),
                                **(
                                    {"phase_to_use": period.phase_to_use}
                                    if period.phase_to_use is not None
                                    else {}
                                ),
                            }
                            for period in profile.schedule.periods
                        ],
                        "min_charging_rate": profile.schedule.min_charging_rate,
                    },
                )
        return charger

    def _diff_reported_charging_profile(
        self,
        expected_profile: ChargingProfile,
        reported_profile: NormalizedChargingProfileReport,
    ) -> list[str]:
        """Return human-readable mismatches between expected and reported profiles."""

        expected_normalized = self._normalized_profile_payload(
            self._model_profile_to_normalized(expected_profile)
        )
        reported_normalized = self._normalized_profile_payload(reported_profile)

        mismatches: list[str] = []

        def _compare_field(key: str, label: str) -> None:
            if expected_normalized.get(key) != reported_normalized.get(key):
                mismatches.append(
                    f"{label} expected {expected_normalized.get(key)} got {reported_normalized.get(key)}"
                )

        _compare_field("stackLevel", "stack level")
        _compare_field("purpose", "purpose")
        _compare_field("kind", "kind")
        _compare_field("recurrencyKind", "recurrency kind")
        _compare_field("transactionId", "transaction id")
        _compare_field("validFrom", "valid from")
        _compare_field("validTo", "valid to")

        expected_schedule = expected_normalized.get("schedule", {})
        reported_schedule = reported_normalized.get("schedule", {})
        for key, label in (
            ("chargingRateUnit", "charging rate unit"),
            ("duration", "duration"),
            ("startSchedule", "start schedule"),
            ("minChargingRate", "min charging rate"),
        ):
            if expected_schedule.get(key) != reported_schedule.get(key):
                mismatches.append(
                    f"{label} expected {expected_schedule.get(key)} got {reported_schedule.get(key)}"
                )

        expected_periods = expected_schedule.get("periods", [])
        reported_periods = reported_schedule.get("periods", [])
        if len(expected_periods) != len(reported_periods):
            mismatches.append(
                f"period count expected {len(expected_periods)} got {len(reported_periods)}"
            )
        else:
            for index, (expected_period, reported_period) in enumerate(
                zip(expected_periods, reported_periods), start=1
            ):
                if expected_period != reported_period:
                    mismatches.append(
                        f"period {index} expected {expected_period} got {reported_period}"
                    )

        return mismatches

    def _reconcile_reported_charging_profiles(
        self,
        report: NormalizedChargingProfileReportPayload,
        *,
        charger: Charger | None,
    ) -> None:
        """Compare reported charging profiles against locally stored profiles."""

        if charger is None:
            return

        evse_label = store.connector_slug(report.evse_id)
        expected_profiles = ChargingProfile.objects.filter(
            charger__charger_id=charger.charger_id
        )
        if report.evse_id is not None:
            expected_profiles = expected_profiles.filter(
                charger__connector_id=report.evse_id
            )
        expected_by_id = {
            entry.charging_profile_id: entry for entry in expected_profiles
        }

        mismatches: list[str] = []
        seen_profile_ids: set[int] = set()
        for profile in report.profiles:
            if profile.profile_id in seen_profile_ids:
                mismatches.append(
                    f"duplicate profile {profile.profile_id} reported for evse {evse_label}"
                )
                continue
            seen_profile_ids.add(profile.profile_id)
            store.record_reported_charging_profile(
                charger.charger_id,
                request_id=report.request_id,
                evse_id=report.evse_id,
                profile_id=profile.profile_id,
            )
            expected_profile = expected_by_id.get(profile.profile_id)
            if expected_profile is None:
                mismatches.append(
                    f"unexpected profile {profile.profile_id} reported for evse {evse_label}"
                )
                continue
            mismatches.extend(
                self._diff_reported_charging_profile(expected_profile, profile)
            )

        if mismatches:
            request_label = (
                f"request {report.request_id}"
                if report.request_id is not None
                else "request ?"
            )
            store.add_log(
                self.store_key,
                f"ReportChargingProfiles mismatch ({request_label}, evse {evse_label}): {', '.join(mismatches)}",
                log_type="charger",
            )

        if report.tbc:
            return

        recorded = store.consume_reported_charging_profiles(
            charger.charger_id, request_id=report.request_id
        )
        reported_by_evse = recorded.get("reported") if recorded else {}

        expected_all = ChargingProfile.objects.filter(
            charger__charger_id=charger.charger_id
        )
        expected_by_evse: dict[str, set[int]] = {}
        for entry in expected_all:
            key = store.connector_slug(entry.connector_id)
            expected_by_evse.setdefault(key, set()).add(entry.charging_profile_id)

        for evse_key, expected_ids in expected_by_evse.items():
            reported_ids = reported_by_evse.get(evse_key, set())
            missing = sorted(expected_ids - set(reported_ids))
            if missing:
                request_label = (
                    f"request {report.request_id}"
                    if report.request_id is not None
                    else "request ?"
                )
                store.add_log(
                    self.store_key,
                    f"ReportChargingProfiles missing ({request_label}, evse {evse_key}): "
                    + ", ".join(str(value) for value in missing),
                    log_type="charger",
                )

    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "ReportChargingProfiles")
    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "ReportChargingProfiles")
    async def _handle_report_charging_profiles_action(
        self, payload, msg_id, raw, text_data
    ):
        try:
            report = self._validate_report_charging_profiles_payload(payload)
        except ReportChargingProfilesValidationError as exc:
            store.add_log(
                self.store_key,
                f"ReportChargingProfiles ignored: {exc}",
                log_type="charger",
            )
            return {}

        def _persist_and_reconcile() -> None:
            charger = self._resolve_report_charging_profile_charger(report)
            self._reconcile_reported_charging_profiles(report, charger=charger)
            self._persist_reported_charging_profiles(report, charger=charger)

        try:
            await database_sync_to_async(_persist_and_reconcile)()
            self._log_ocpp201_notification("ReportChargingProfiles", payload)
        except ValidationError as exc:
            store.add_log(
                self.store_key,
                f"ReportChargingProfiles ignored: {exc}",
                log_type="charger",
            )
        return {}
