import json
import uuid

from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import CPNetworkProfile, CPNetworkProfileDeployment
from .common import (
    CALL_EXPECTED_STATUSES,
    ActionCall,
    ActionContext,
    _get_or_create_charger,
)


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "SetNetworkProfile")
def _handle_set_network_profile(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    profile_id_value = (
        data.get("networkProfileId")
        or data.get("network_profile_id")
        or data.get("profileId")
        or data.get("profile")
    )
    try:
        profile_id = int(profile_id_value)
    except (TypeError, ValueError):
        return JsonResponse({"detail": "networkProfileId required"}, status=400)
    network_profile = CPNetworkProfile.objects.filter(pk=profile_id).first()
    if network_profile is None:
        return JsonResponse({"detail": "network profile not found"}, status=404)
    charger = context.charger or _get_or_create_charger(context.cid, context.connector_value)
    if charger is None:
        return JsonResponse({"detail": "charger not found"}, status=404)
    payload = network_profile.build_payload()
    message_id = uuid.uuid4().hex
    deployment = CPNetworkProfileDeployment.objects.create(
        network_profile=network_profile,
        charger=charger,
        node=charger.node_origin if charger else None,
        ocpp_message_id=message_id,
        status="Pending",
        status_info=_("Awaiting charge point response."),
        status_timestamp=timezone.now(),
        request_payload=payload,
    )
    ocpp_action = "SetNetworkProfile"
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
            "requested_at": timezone.now(),
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=context.log_key,
        message="SetNetworkProfile request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
    )
