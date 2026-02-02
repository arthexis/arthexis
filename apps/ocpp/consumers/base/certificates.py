import json
import uuid

from django.utils import timezone
from channels.db import database_sync_to_async
from apps.protocols.decorators import protocol_call
from apps.protocols.models import ProtocolCall as ProtocolCallModel

from ... import store
from ...models import (
    CertificateOperation,
    CertificateRequest,
    CertificateStatusCheck,
    Charger,
    InstalledCertificate,
)
from ...services import certificate_signing


class CertificatesMixin:
    def _resolve_certificate_target(self) -> Charger | None:
        target = self.aggregate_charger or self.charger
        if target and target.pk:
            found = Charger.objects.filter(pk=target.pk).first()
            if found:
                return found

        charger_id = ""
        if target and getattr(target, "charger_id", ""):
            charger_id = target.charger_id
        elif getattr(self, "charger_id", ""):
            charger_id = self.charger_id

        if charger_id:
            found = Charger.objects.filter(charger_id=charger_id).first()
            if found:
                return found
            return Charger.objects.create(charger_id=charger_id)

        return None

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "Get15118EVCertificate")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "Get15118EVCertificate")
    async def _handle_get_15118_ev_certificate_action(
        self, payload, msg_id, raw, text_data
    ):
        certificate_type = str(payload.get("certificateType") or "").strip()
        csr_value = payload.get("exiRequest") or payload.get("csr")
        if csr_value is None:
            csr_value = ""
        if not isinstance(csr_value, str):
            csr_value = str(csr_value)
        csr_value = csr_value.strip()

        def _csr_is_valid(value: str) -> bool:
            return bool(value)

        responded_at = timezone.now()

        def _handle_request():
            target = self._resolve_certificate_target()
            response_payload: dict[str, object]
            exi_response = ""
            request_status = CertificateRequest.STATUS_REJECTED
            status_info = ""

            if target is None:
                status_info = "Unknown charge point."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "Failed",
                        "additionalInfo": status_info,
                    },
                }
            elif not _csr_is_valid(csr_value):
                status_info = "EXI request payload is missing or invalid."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "FormatViolation",
                        "additionalInfo": status_info,
                    },
                }
            else:
                try:
                    exi_response = certificate_signing.sign_certificate_request(
                        csr=csr_value,
                        certificate_type=certificate_type,
                        charger_id=target.charger_id,
                    )
                    response_payload = {
                        "status": "Accepted",
                        "exiResponse": exi_response,
                    }
                    request_status = CertificateRequest.STATUS_ACCEPTED
                    status_info = ""
                except certificate_signing.CertificateSigningError as exc:
                    status_info = str(exc) or "Certificate request failed."
                    response_payload = {
                        "status": "Rejected",
                        "statusInfo": {
                            "reasonCode": "Failed",
                            "additionalInfo": status_info,
                        },
                    }
                    request_status = CertificateRequest.STATUS_ERROR

            request_pk: int | None = None
            if target is not None:
                request = CertificateRequest.objects.create(
                    charger=target,
                    action=CertificateRequest.ACTION_15118,
                    certificate_type=certificate_type,
                    csr=csr_value,
                    signed_certificate=exi_response,
                    status=request_status,
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=responded_at,
                )
                request_pk = request.pk

            return {
                "response": response_payload,
                "status": request_status,
                "request_pk": request_pk,
            }

        result = await database_sync_to_async(_handle_request)()
        response_payload = result.get("response", {})
        status_value = response_payload.get("status") or "Unknown"
        store.add_log(
            self.store_key,
            f"Get15118EVCertificate request processed (status={status_value}).",
            log_type="charger",
        )
        return response_payload

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "GetCertificateStatus")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "GetCertificateStatus")
    async def _handle_get_certificate_status_action(
        self, payload, msg_id, raw, text_data
    ):
        hash_data = payload.get("certificateHashData") or {}
        if not isinstance(hash_data, dict):
            hash_data = {}

        def _persist_status() -> dict:
            target = self._resolve_certificate_target()
            status_value = "Failed"
            status_info = "Unknown charge point."
            response_payload: dict[str, object] = {"status": status_value}

            if target is not None:
                status_info = "Certificate not found."
                installed = InstalledCertificate.objects.filter(
                    charger=target, certificate_hash_data=hash_data
                ).first()
                if installed and installed.status == InstalledCertificate.STATUS_INSTALLED:
                    status_value = "Accepted"
                    status_info = ""
                    response_payload = {"status": status_value}
                else:
                    response_payload = {
                        "status": status_value,
                        "statusInfo": {
                            "reasonCode": "NotFound",
                            "additionalInfo": status_info,
                        },
                    }

                CertificateStatusCheck.objects.create(
                    charger=target,
                    certificate_hash_data=hash_data,
                    ocsp_result={},
                    status=(
                        CertificateStatusCheck.STATUS_ACCEPTED
                        if status_value == "Accepted"
                        else CertificateStatusCheck.STATUS_REJECTED
                    ),
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=timezone.now(),
                )

            return {
                "response": response_payload,
                "status": status_value,
                "status_info": status_info,
            }

        result = await database_sync_to_async(_persist_status)()
        status_value = result.get("status")
        store.add_log(
            self.store_key,
            f"GetCertificateStatus request received (status={status_value}).",
            log_type="charger",
        )
        return result.get("response")

    @protocol_call("ocpp201", ProtocolCallModel.CP_TO_CSMS, "SignCertificate")
    @protocol_call("ocpp21", ProtocolCallModel.CP_TO_CSMS, "SignCertificate")
    async def _handle_sign_certificate_action(
        self, payload, msg_id, raw, text_data
    ):
        csr_value = payload.get("csr")
        if csr_value is None:
            csr_value = ""
        if not isinstance(csr_value, str):
            csr_value = str(csr_value)
        csr_value = csr_value.strip()
        certificate_type = str(payload.get("certificateType") or "").strip()

        def _csr_is_valid(value: str) -> bool:
            return bool(value)

        responded_at = timezone.now()

        def _handle_signing():
            target = self._resolve_certificate_target()
            response_payload: dict[str, object]
            signed_certificate = ""
            request_status = CertificateRequest.STATUS_REJECTED
            status_info = ""
            request_pk: int | None = None

            if target is None:
                status_info = "Unknown charge point."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "Failed",
                        "additionalInfo": status_info,
                    },
                }
            elif not _csr_is_valid(csr_value):
                status_info = "CSR payload is missing or invalid."
                response_payload = {
                    "status": "Rejected",
                    "statusInfo": {
                        "reasonCode": "FormatViolation",
                        "additionalInfo": status_info,
                    },
                }
            else:
                try:
                    signed_certificate = certificate_signing.sign_certificate_request(
                        csr=csr_value,
                        certificate_type=certificate_type,
                        charger_id=target.charger_id,
                    )
                    response_payload = {"status": "Accepted"}
                    request_status = CertificateRequest.STATUS_ACCEPTED
                    status_info = ""
                except certificate_signing.CertificateSigningError as exc:
                    status_info = str(exc) or "Certificate signing failed."
                    response_payload = {
                        "status": "Rejected",
                        "statusInfo": {
                            "reasonCode": "Failed",
                            "additionalInfo": status_info,
                        },
                    }
                    request_status = CertificateRequest.STATUS_ERROR

            if target is not None:
                request = CertificateRequest.objects.create(
                    charger=target,
                    action=CertificateRequest.ACTION_SIGN,
                    certificate_type=certificate_type,
                    csr=csr_value,
                    signed_certificate=signed_certificate,
                    status=request_status,
                    status_info=status_info,
                    request_payload=payload,
                    response_payload=response_payload,
                    responded_at=responded_at,
                )
                request_pk = request.pk

            return {
                "response": response_payload,
                "target": target,
                "request_pk": request_pk,
                "signed_certificate": signed_certificate,
            }

        result = await database_sync_to_async(_handle_signing)()
        response_payload: dict[str, object] = result.get("response", {})
        status_value = str(response_payload.get("status") or "Unknown")
        target: Charger | None = result.get("target")
        signed_certificate = result.get("signed_certificate") or ""
        request_pk = result.get("request_pk")

        if (
            status_value.lower() == "accepted"
            and target is not None
            and signed_certificate
        ):
            await self._dispatch_certificate_signed(
                target,
                certificate_chain=signed_certificate,
                certificate_type=certificate_type,
                request_pk=request_pk,
            )

        store.add_log(
            self.store_key,
            f"SignCertificate request processed (status={status_value}).",
            log_type="charger",
        )
        return response_payload

    async def _dispatch_certificate_signed(
        self,
        charger: Charger,
        *,
        certificate_chain: str,
        certificate_type: str = "",
        request_pk: int | None = None,
    ) -> None:
        payload = {"certificateChain": certificate_chain}
        if certificate_type:
            payload["certificateType"] = certificate_type
        message_id = uuid.uuid4().hex
        msg = json.dumps([2, message_id, "CertificateSigned", payload])
        await self.send(msg)

        log_key = self.store_key or store.identity_key(
            charger.charger_id, getattr(charger, "connector_id", None)
        )
        requested_at = timezone.now()
        operation = await database_sync_to_async(CertificateOperation.objects.create)(
            charger=charger,
            action=CertificateOperation.ACTION_SIGNED,
            certificate_type=certificate_type,
            request_payload=payload,
            status=CertificateOperation.STATUS_PENDING,
        )
        if request_pk:
            await database_sync_to_async(CertificateRequest.objects.filter(pk=request_pk).update)(
                signed_certificate=certificate_chain,
                status=CertificateRequest.STATUS_PENDING,
                status_info="Certificate sent to charge point.",
            )
        store.register_pending_call(
            message_id,
            {
                "action": "CertificateSigned",
                "charger_id": charger.charger_id,
                "connector_id": getattr(charger, "connector_id", None),
                "log_key": log_key,
                "requested_at": requested_at,
                "operation_pk": operation.pk,
            },
        )
        store.schedule_call_timeout(
            message_id,
            action="CertificateSigned",
            log_key=log_key,
            message="CertificateSigned request timed out",
        )


__all__ = ["CertificatesMixin"]
