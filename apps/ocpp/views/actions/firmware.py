import json
import uuid
from datetime import timedelta

from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import CPFirmware, CPFirmwareDeployment
from .common import CALL_EXPECTED_STATUSES, ActionCall, ActionContext


@protocol_call("ocpp21", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware")
@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "UpdateFirmware")
def _handle_update_firmware(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    firmware_id = (
        data.get("firmwareId")
        or data.get("firmware_id")
        or data.get("firmware")
    )
    try:
        firmware_pk = int(firmware_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "firmwareId required"}, status=400)

    firmware = CPFirmware.objects.filter(pk=firmware_pk).first()
    if firmware is None:
        return JsonResponse({"detail": "firmware not found"}, status=404)
    if not firmware.has_binary and not firmware.has_json:
        return JsonResponse({"detail": "firmware payload missing"}, status=400)

    retrieve_raw = data.get("retrieveDate") or data.get("retrieve_date")
    if retrieve_raw:
        retrieve_date = parse_datetime(str(retrieve_raw))
        if retrieve_date is None:
            return JsonResponse({"detail": "invalid retrieveDate"}, status=400)
        if timezone.is_naive(retrieve_date):
            retrieve_date = timezone.make_aware(
                retrieve_date, timezone.get_current_timezone()
            )
    else:
        retrieve_date = timezone.now() + timedelta(seconds=30)

    retries_value: int | None = None
    if "retries" in data or "retryCount" in data or "retry_count" in data:
        retries_raw = (
            data.get("retries")
            if "retries" in data
            else data.get("retryCount")
            if "retryCount" in data
            else data.get("retry_count")
        )
        try:
            retries_value = int(retries_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retries"}, status=400)
        if retries_value < 0:
            return JsonResponse({"detail": "invalid retries"}, status=400)

    retry_interval_value: int | None = None
    if "retryInterval" in data or "retry_interval" in data:
        retry_interval_raw = data.get("retryInterval")
        if retry_interval_raw is None:
            retry_interval_raw = data.get("retry_interval")
        try:
            retry_interval_value = int(retry_interval_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)
        if retry_interval_value < 0:
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)

    message_id = uuid.uuid4().hex
    deployment = CPFirmwareDeployment.objects.create(
        firmware=firmware,
        charger=context.charger,
        node=context.charger.node_origin if context.charger else None,
        ocpp_message_id=message_id,
        status="Pending",
        status_info=_("Awaiting charge point response."),
        status_timestamp=timezone.now(),
        retrieve_date=retrieve_date,
        retry_count=int(retries_value or 0),
        retry_interval=int(retry_interval_value or 0),
        request_payload={},
        is_user_data=True,
    )
    token = deployment.issue_download_token(lifetime=timedelta(hours=4))
    download_path = reverse("ocpp:cp-firmware-download", args=[deployment.pk, token])
    request_obj = getattr(context, "request", None)
    if request_obj is not None:
        download_url = request_obj.build_absolute_uri(download_path)
    else:  # pragma: no cover - defensive fallback for unusual call contexts
        download_url = download_path
    payload = {
        "location": download_url,
        "retrieveDate": retrieve_date.isoformat(),
    }
    if retries_value is not None:
        payload["retries"] = retries_value
    if retry_interval_value is not None:
        payload["retryInterval"] = retry_interval_value
    if firmware.checksum:
        payload["checksum"] = firmware.checksum
    deployment.request_payload = payload
    deployment.save(update_fields=["request_payload", "updated_at"])

    ocpp_action = "UpdateFirmware"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "UpdateFirmware", payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "deployment_pk": deployment.pk,
            "log_key": context.log_key,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="UpdateFirmware request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=context.log_key,
    )


@protocol_call("ocpp21", ProtocolCallModel.CSMS_TO_CP, "PublishFirmware")
@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "PublishFirmware")
def _handle_publish_firmware(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    firmware_id = (
        data.get("firmwareId")
        or data.get("firmware_id")
        or data.get("firmware")
    )
    try:
        firmware_pk = int(firmware_id)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "firmwareId required"}, status=400)

    firmware = CPFirmware.objects.filter(pk=firmware_pk).first()
    if firmware is None:
        return JsonResponse({"detail": "firmware not found"}, status=404)
    if not firmware.has_binary and not firmware.has_json:
        return JsonResponse({"detail": "firmware payload missing"}, status=400)

    retries_value: int | None = None
    if "retries" in data or "retryCount" in data or "retry_count" in data:
        retries_raw = (
            data.get("retries")
            if "retries" in data
            else data.get("retryCount")
            if "retryCount" in data
            else data.get("retry_count")
        )
        try:
            retries_value = int(retries_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retries"}, status=400)
        if retries_value < 0:
            return JsonResponse({"detail": "invalid retries"}, status=400)

    retry_interval_value: int | None = None
    if "retryInterval" in data or "retry_interval" in data:
        retry_interval_raw = data.get("retryInterval")
        if retry_interval_raw is None:
            retry_interval_raw = data.get("retry_interval")
        try:
            retry_interval_value = int(retry_interval_raw)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)
        if retry_interval_value < 0:
            return JsonResponse({"detail": "invalid retryInterval"}, status=400)

    message_id = uuid.uuid4().hex
    deployment = CPFirmwareDeployment.objects.create(
        firmware=firmware,
        charger=context.charger,
        node=context.charger.node_origin if context.charger else None,
        ocpp_message_id=message_id,
        status="Pending",
        status_info=_("Awaiting charge point response."),
        status_timestamp=timezone.now(),
        request_payload={},
        is_user_data=True,
    )
    token = deployment.issue_download_token(lifetime=timedelta(hours=4))
    download_path = reverse("ocpp:cp-firmware-download", args=[deployment.pk, token])
    request_obj = getattr(context, "request", None)
    if request_obj is not None:
        download_url = request_obj.build_absolute_uri(download_path)
    else:  # pragma: no cover - defensive fallback for unusual call contexts
        download_url = download_path
    payload = {
        "location": download_url,
        "requestId": deployment.pk,
    }
    if retries_value is not None:
        payload["retries"] = retries_value
    if retry_interval_value is not None:
        payload["retryInterval"] = retry_interval_value
    if firmware.checksum:
        payload["checksum"] = firmware.checksum
    deployment.request_payload = payload
    deployment.save(update_fields=["request_payload", "updated_at"])

    ocpp_action = "PublishFirmware"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "deployment_pk": deployment.pk,
            "log_key": context.log_key,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="PublishFirmware request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=context.log_key,
    )


@protocol_call("ocpp21", ProtocolCallModel.CSMS_TO_CP, "UnpublishFirmware")
@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "UnpublishFirmware")
def _handle_unpublish_firmware(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    firmware_id = (
        data.get("firmwareId")
        or data.get("firmware_id")
        or data.get("firmware")
    )
    firmware = None
    firmware_pk = None
    if firmware_id not in (None, ""):
        try:
            firmware_pk = int(firmware_id)
        except (TypeError, ValueError):
            return JsonResponse({"detail": "invalid firmwareId"}, status=400)
        firmware = CPFirmware.objects.filter(pk=firmware_pk).first()
        if firmware is None:
            return JsonResponse({"detail": "firmware not found"}, status=404)

    checksum = data.get("checksum") or (firmware.checksum if firmware else "")
    if not checksum:
        return JsonResponse({"detail": "checksum required"}, status=400)

    payload = {"checksum": checksum}
    request_id = data.get("requestId") or data.get("request_id")
    if request_id is None and firmware_pk is not None:
        request_id = firmware_pk
    if request_id is not None:
        payload["requestId"] = request_id

    message_id = uuid.uuid4().hex
    ocpp_action = "UnpublishFirmware"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "firmware_pk": firmware_pk,
            "log_key": context.log_key,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="UnpublishFirmware request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=context.log_key,
    )
