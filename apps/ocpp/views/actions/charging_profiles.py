import json
import uuid

from django.http import JsonResponse
from django.utils import timezone

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import ChargingProfile, PowerProjection
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _get_or_create_charger,
)


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetCompositeSchedule")
def _handle_get_composite_schedule(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    duration_raw = data.get("duration") or data.get("durationSeconds")
    try:
        duration_value = int(duration_raw)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "duration must be an integer"}, status=400)
    if duration_value <= 0:
        return JsonResponse({"detail": "duration must be positive"}, status=400)

    evse_value = data.get("evseId")
    if evse_value in (None, ""):
        evse_value = data.get("connectorId")
    if evse_value in (None, ""):
        evse_value = context.connector_value
    evse_payload: int | None = None
    if evse_value not in (None, ""):
        try:
            evse_payload = int(evse_value)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "evseId must be an integer"}, status=400)

    payload: dict[str, object] = {"duration": duration_value}
    if evse_payload is not None:
        payload["evseId"] = evse_payload
    rate_unit = data.get("chargingRateUnit") or ChargingProfile.RateUnit.WATT
    if rate_unit:
        payload["chargingRateUnit"] = rate_unit

    connector_value = evse_payload if evse_payload is not None else (context.connector_value or 0)
    charger = context.charger or _get_or_create_charger(context.cid, connector_value)
    if charger is None:
        return JsonResponse({"detail": "charger not found"}, status=404)

    projection = PowerProjection.objects.create(
        charger=charger,
        connector_id=connector_value,
        duration_seconds=duration_value,
        charging_rate_unit=rate_unit,
    )

    message_id = uuid.uuid4().hex
    ocpp_action = "GetCompositeSchedule"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)

    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "projection_pk": projection.pk,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        timeout=5.0,
        action=ocpp_action,
        log_key=log_key,
        message=(
            "GetCompositeSchedule timed out: charger did not respond"
            " (operation may not be supported)"
        ),
    )

    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetChargingProfile")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "SetChargingProfile")
def _handle_set_charging_profile(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    profile_id = (
        data.get("profileId")
        or data.get("profile_id")
        or data.get("profile")
        or data.get("chargingProfile")
    )
    try:
        profile_pk = int(profile_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "profileId required"}, status=400)

    profile = (
        ChargingProfile.objects.select_related("schedule")
        .filter(pk=profile_pk)
        .first()
    )
    if profile is None:
        return JsonResponse({"detail": "charging profile not found"}, status=404)

    connector_value: int | str | None = profile.connector_id
    connector_raw = data.get("connectorId")
    if connector_raw not in (None, ""):
        try:
            connector_value = int(connector_raw)
        except (TypeError, ValueError):
            connector_value = connector_raw
    elif context.connector_value is not None:
        connector_value = context.connector_value

    schedule_override = data.get("schedule") or data.get("chargingSchedule")
    if schedule_override is not None and not isinstance(schedule_override, dict):
        return JsonResponse({"detail": "schedule must be an object"}, status=400)

    payload = profile.as_set_charging_profile_request(
        connector_id=connector_value,
        schedule_payload=schedule_override if isinstance(schedule_override, dict) else None,
    )
    message_id = uuid.uuid4().hex
    ocpp_action = "SetChargingProfile"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": connector_value,
            "charging_profile_id": profile_pk,
            "log_key": log_key,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="SetChargingProfile request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ClearChargingProfile")
def _handle_clear_charging_profile(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    charging_profile_id: int | None = None
    stack_level: int | None = None
    evse_id: int | None = None

    def _parse_int(value, label: str) -> int | None | JsonResponse:
        if value in (None, ""):
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return JsonResponse({"detail": f"invalid {label}"}, status=400)
        if parsed < 0:
            return JsonResponse({"detail": f"{label} must be positive"}, status=400)
        return parsed

    parsed = _parse_int(data.get("chargingProfileId"), "chargingProfileId")
    if isinstance(parsed, JsonResponse):
        return parsed
    charging_profile_id = parsed

    parsed = _parse_int(data.get("stackLevel"), "stackLevel")
    if isinstance(parsed, JsonResponse):
        return parsed
    stack_level = parsed

    parsed = _parse_int(data.get("evseId"), "evseId")
    if isinstance(parsed, JsonResponse):
        return parsed
    evse_id = parsed

    criteria = data.get("chargingProfileCriteria")
    criteria_payload: dict[str, object] = {}
    if criteria not in (None, ""):
        if not isinstance(criteria, dict):
            return JsonResponse({"detail": "chargingProfileCriteria must be an object"}, status=400)

        parsed = _parse_int(criteria.get("chargingProfileId"), "chargingProfileId")
        if isinstance(parsed, JsonResponse):
            return parsed
        if parsed is not None:
            criteria_payload["chargingProfileId"] = parsed
            charging_profile_id = charging_profile_id or parsed

        parsed = _parse_int(criteria.get("stackLevel"), "stackLevel")
        if isinstance(parsed, JsonResponse):
            return parsed
        if parsed is not None:
            criteria_payload["stackLevel"] = parsed
            stack_level = stack_level or parsed

        parsed = _parse_int(criteria.get("evseId"), "evseId")
        if isinstance(parsed, JsonResponse):
            return parsed
        if parsed is not None:
            criteria_payload["evseId"] = parsed
            evse_id = evse_id or parsed

        purpose = criteria.get("chargingProfilePurpose")
        if purpose not in (None, ""):
            criteria_payload["chargingProfilePurpose"] = str(purpose)

        if not criteria_payload:
            return JsonResponse({"detail": "chargingProfileCriteria must include a filter"}, status=400)

    if (
        charging_profile_id is None
        and stack_level is None
        and evse_id is None
        and not criteria_payload
    ):
        return JsonResponse(
            {"detail": "chargingProfileId, stackLevel, evseId, or chargingProfileCriteria required"},
            status=400,
        )

    payload: dict[str, object] = {}
    if charging_profile_id is not None:
        payload["chargingProfileId"] = charging_profile_id
    if stack_level is not None:
        payload["stackLevel"] = stack_level
    if evse_id is not None:
        payload["evseId"] = evse_id
    if criteria_payload:
        payload["chargingProfileCriteria"] = criteria_payload

    message_id = uuid.uuid4().hex
    ocpp_action = "ClearChargingProfile"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "charging_profile_id": charging_profile_id,
            "stack_level": stack_level,
            "evse_id": evse_id,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="ClearChargingProfile request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetChargingProfiles")
def _handle_get_charging_profiles(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    request_id_value = data.get("requestId") or data.get("request_id")
    try:
        request_id = int(request_id_value) if request_id_value is not None else None
    except (TypeError, ValueError):
        return JsonResponse({"detail": "requestId must be an integer"}, status=400)
    if request_id is None:
        request_id = int(uuid.uuid4().int % 1_000_000_000)
    evse_id_value = data.get("evseId") or data.get("evse_id")
    evse_id: int | None = None
    if evse_id_value not in (None, ""):
        try:
            evse_id = int(evse_id_value)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "evseId must be an integer"}, status=400)
    charging_profile = data.get("chargingProfile") or data.get("charging_profile")
    if charging_profile is None:
        charging_profile = {}
        profile_id_value = (
            data.get("chargingProfileId")
            or data.get("charging_profile_id")
            or data.get("profileId")
        )
        if profile_id_value not in (None, ""):
            try:
                charging_profile["chargingProfileId"] = int(profile_id_value)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "chargingProfileId must be an integer"}, status=400)
        purpose = data.get("chargingProfilePurpose") or data.get("charging_profile_purpose")
        if purpose not in (None, ""):
            charging_profile["chargingProfilePurpose"] = purpose
        stack_level_value = data.get("stackLevel") or data.get("stack_level")
        if stack_level_value not in (None, ""):
            try:
                charging_profile["stackLevel"] = int(stack_level_value)
            except (TypeError, ValueError):
                return JsonResponse({"detail": "stackLevel must be an integer"}, status=400)
        limit_source = data.get("chargingLimitSource") or data.get("charging_limit_source")
        if limit_source not in (None, ""):
            charging_profile["chargingLimitSource"] = limit_source
    if not isinstance(charging_profile, dict):
        return JsonResponse({"detail": "chargingProfile must be an object"}, status=400)
    payload: dict[str, object] = {"requestId": request_id, "chargingProfile": charging_profile}
    if evse_id is not None:
        payload["evseId"] = evse_id
    message_id = uuid.uuid4().hex
    ocpp_action = "GetChargingProfiles"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": context.log_key,
            "requested_at": timezone.now(),
            "request_id": request_id,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="GetChargingProfiles request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
