import json
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import CPReservation
from .common import CALL_EXPECTED_STATUSES, ActionCall, ActionContext


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "ReserveNow")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "ReserveNow")
def _handle_reserve_now(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    reservation_pk = data.get("reservation") or data.get("reservationId")
    if reservation_pk in (None, ""):
        return JsonResponse({"detail": "reservation required"}, status=400)
    reservation = CPReservation.objects.filter(pk=reservation_pk).first()
    if reservation is None:
        return JsonResponse({"detail": "reservation not found"}, status=404)
    connector_obj = reservation.connector
    if connector_obj is None or connector_obj.connector_id is None:
        detail = _("Unable to determine which connector to reserve.")
        return JsonResponse({"detail": detail}, status=400)
    id_tag = reservation.id_tag_value
    if not id_tag:
        detail = _("Provide an RFID or idTag before creating the reservation.")
        return JsonResponse({"detail": detail}, status=400)
    connector_value = connector_obj.connector_id
    log_key = store.identity_key(context.cid, connector_value)
    ws = store.get_connection(context.cid, connector_value)
    if ws is None:
        return JsonResponse({"detail": "no connection"}, status=404)
    expiry = timezone.localtime(reservation.end_time)
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    payload = {
        "connectorId": connector_value,
        "expiryDate": expiry.isoformat(),
        "idTag": id_tag,
        "reservationId": reservation.pk,
    }
    if ocpp_version.startswith("ocpp2.0"):
        payload = {
            "id": reservation.pk,
            "expiryDateTime": expiry.isoformat(),
            "idToken": {"idToken": id_tag, "type": "Central"},
        }
        if connector_value not in (None, ""):
            payload["evseId"] = connector_value
    message_id = uuid.uuid4().hex
    ocpp_action = "ReserveNow"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, "ReserveNow", payload])
    store.add_log(
        log_key,
        f"ReserveNow request: reservation={reservation.pk}, expiry={expiry.isoformat()}",
        log_type="charger",
    )
    async_to_sync(ws.send)(msg)
    requested_at = timezone.now()
    store.register_pending_call(
        message_id,
        {
            "action": "ReserveNow",
            "charger_id": context.cid,
            "connector_id": connector_value,
            "log_key": log_key,
            "reservation_pk": reservation.pk,
            "requested_at": requested_at,
        },
    )
    store.schedule_call_timeout(message_id, action="ReserveNow", log_key=log_key)
    reservation.ocpp_message_id = message_id
    reservation.evcs_status = ""
    reservation.evcs_error = ""
    reservation.evcs_confirmed = False
    reservation.evcs_confirmed_at = None
    reservation.save(
        update_fields=[
            "ocpp_message_id",
            "evcs_status",
            "evcs_error",
            "evcs_confirmed",
            "evcs_confirmed_at",
            "updated_on",
        ]
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "CancelReservation")
@protocol_call("ocpp16", ProtocolCallModel.CSMS_TO_CP, "CancelReservation")
def _handle_cancel_reservation(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    reservation_pk = data.get("reservation") or data.get("reservationId")
    if reservation_pk in (None, ""):
        return JsonResponse({"detail": "reservation required"}, status=400)
    reservation = CPReservation.objects.filter(pk=reservation_pk).first()
    if reservation is None:
        return JsonResponse({"detail": "reservation not found"}, status=404)
    message_id = uuid.uuid4().hex
    ocpp_version = str(getattr(context.ws, "ocpp_version", "") or "")
    payload = {"reservationId": reservation.pk}
    ocpp_action = "CancelReservation"
    if ocpp_version.startswith("ocpp2.0"):
        payload = {"id": reservation.pk}
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
            "reservation_pk": reservation.pk,
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="CancelReservation request timed out",
    )
    reservation.ocpp_message_id = message_id
    reservation.evcs_status = ""
    reservation.evcs_error = ""
    reservation.save(
        update_fields=["ocpp_message_id", "evcs_status", "evcs_error", "updated_on"]
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
