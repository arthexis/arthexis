"""Call result handlers for schedule actions."""
from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import PowerProjection
from ...utils import _parse_ocpp_timestamp
from ..types import CallResultContext


async def handle_get_composite_schedule_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    projection_pk = metadata.get("projection_pk")
    status_value = str(payload_data.get("status") or "").strip()
    schedule_payload = (
        payload_data.get("chargingSchedule") if isinstance(payload_data, dict) else {}
    )
    schedule_start = _parse_ocpp_timestamp(payload_data.get("scheduleStart"))
    duration_value: int | None = None
    rate_unit_value = ""
    periods: list[dict[str, object]] = []
    if isinstance(schedule_payload, dict):
        try:
            duration_value = (
                int(schedule_payload.get("duration"))
                if schedule_payload.get("duration") is not None
                else None
            )
        except (TypeError, ValueError):
            duration_value = None
        rate_unit_value = str(schedule_payload.get("chargingRateUnit") or "").strip()
        raw_periods = schedule_payload.get("chargingSchedulePeriod")
        if isinstance(raw_periods, (list, tuple)):
            for entry in raw_periods:
                if not isinstance(entry, dict):
                    continue
                try:
                    start_period = int(entry.get("startPeriod"))
                except (TypeError, ValueError):
                    continue
                period: dict[str, object] = {
                    "start_period": start_period,
                    "limit": entry.get("limit"),
                }
                if entry.get("numberPhases") is not None:
                    period["number_phases"] = entry.get("numberPhases")
                if entry.get("phaseToUse") is not None:
                    period["phase_to_use"] = entry.get("phaseToUse")
                periods.append(period)

    def _apply() -> PowerProjection | None:
        if not projection_pk:
            return None
        projection = (
            PowerProjection.objects.filter(pk=projection_pk)
            .select_related("charger")
            .first()
        )
        if not projection:
            return None
        projection.status = status_value
        projection.schedule_start = schedule_start
        projection.duration_seconds = duration_value
        projection.charging_rate_unit = rate_unit_value
        projection.charging_schedule_periods = periods
        projection.raw_response = payload_data
        projection.received_at = timezone.now()
        projection.save(
            update_fields=[
                "status",
                "schedule_start",
                "duration_seconds",
                "charging_rate_unit",
                "charging_schedule_periods",
                "raw_response",
                "received_at",
                "updated_at",
            ]
        )
        return projection

    await database_sync_to_async(_apply)()

    message = "GetCompositeSchedule result"
    if status_value:
        message += f": status={status_value}"
    store.add_log(log_key, message, log_type="charger")
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True
