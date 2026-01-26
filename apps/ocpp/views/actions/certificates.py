import json
import uuid

from django.http import JsonResponse
from django.utils import timezone

from asgiref.sync import async_to_sync

from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import CertificateOperation, CertificateRequest, InstalledCertificate
from .common import CALL_EXPECTED_STATUSES, ActionCall, ActionContext


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "InstallCertificate")
def _handle_install_certificate(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    certificate = data.get("certificate")
    if not isinstance(certificate, str) or not certificate.strip():
        return JsonResponse({"detail": "certificate required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificate": certificate.strip()}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "InstallCertificate"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_INSTALL,
        certificate_type=certificate_type,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    installed_certificate = InstalledCertificate.objects.create(
        charger=context.charger,
        certificate_type=certificate_type,
        certificate=certificate.strip(),
        status=InstalledCertificate.STATUS_PENDING,
        last_action=CertificateOperation.ACTION_INSTALL,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
            "installed_certificate_pk": installed_certificate.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="InstallCertificate request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "DeleteCertificate")
def _handle_delete_certificate(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    hash_data = data.get("certificateHashData")
    if not isinstance(hash_data, dict) or not hash_data:
        return JsonResponse({"detail": "certificateHashData required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificateHashData": hash_data}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "DeleteCertificate"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_DELETE,
        certificate_type=certificate_type,
        certificate_hash_data=hash_data,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    installed_cert = InstalledCertificate.objects.filter(
        charger=context.charger,
        certificate_hash_data=hash_data,
    ).first()
    if installed_cert:
        installed_cert.status = InstalledCertificate.STATUS_DELETE_PENDING
        installed_cert.last_action = CertificateOperation.ACTION_DELETE
        installed_cert.save(update_fields=["status", "last_action"])
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
            "installed_certificate_pk": installed_cert.pk if installed_cert else None,
            "certificate_hash_data": hash_data,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="DeleteCertificate request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "CertificateSigned")
def _handle_certificate_signed(context: ActionContext, data: dict) -> JsonResponse | ActionCall:
    certificate_chain = data.get("certificateChain")
    if not isinstance(certificate_chain, str) or not certificate_chain.strip():
        return JsonResponse({"detail": "certificateChain required"}, status=400)
    certificate_type = str(data.get("certificateType") or "").strip()
    payload = {"certificateChain": certificate_chain.strip()}
    if certificate_type:
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "CertificateSigned"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_SIGNED,
        certificate_type=certificate_type,
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    request_pk = data.get("requestId") or data.get("certificateRequest")
    if request_pk not in (None, ""):
        try:
            request_pk_value = int(request_pk)
        except (TypeError, ValueError):
            request_pk_value = None
        if request_pk_value:
            CertificateRequest.objects.filter(pk=request_pk_value).update(
                signed_certificate=certificate_chain.strip(),
                status=CertificateRequest.STATUS_PENDING,
                status_info="Certificate sent to charge point.",
            )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="CertificateSigned request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )


@protocol_call("ocpp201", ProtocolCallModel.CSMS_TO_CP, "GetInstalledCertificateIds")
def _handle_get_installed_certificate_ids(
    context: ActionContext, data: dict
) -> JsonResponse | ActionCall:
    certificate_type = data.get("certificateType")
    payload: dict[str, object] = {}
    if certificate_type not in (None, ""):
        payload["certificateType"] = certificate_type
    message_id = uuid.uuid4().hex
    ocpp_action = "GetInstalledCertificateIds"
    expected_statuses = CALL_EXPECTED_STATUSES.get(ocpp_action)
    msg = json.dumps([2, message_id, ocpp_action, payload])
    async_to_sync(context.ws.send)(msg)
    log_key = context.log_key
    requested_at = timezone.now()
    operation = CertificateOperation.objects.create(
        charger=context.charger,
        action=CertificateOperation.ACTION_LIST,
        certificate_type=str(certificate_type or "").strip(),
        request_payload=payload,
        status=CertificateOperation.STATUS_PENDING,
    )
    store.register_pending_call(
        message_id,
        {
            "action": ocpp_action,
            "charger_id": context.cid,
            "connector_id": context.connector_value,
            "log_key": log_key,
            "requested_at": requested_at,
            "operation_pk": operation.pk,
        },
    )
    store.schedule_call_timeout(
        message_id,
        action=ocpp_action,
        log_key=log_key,
        message="GetInstalledCertificateIds request timed out",
    )
    return ActionCall(
        msg=msg,
        message_id=message_id,
        ocpp_action=ocpp_action,
        expected_statuses=expected_statuses,
        log_key=log_key,
    )
