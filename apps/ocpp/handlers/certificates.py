from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from .. import store
from ..models import CertificateOperation, Charger, InstalledCertificate
from .types import CallErrorContext, CallResultContext
from .utils import _format_status_info, _json_details


async def handle_install_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            if status_value.casefold() == "accepted":
                installed.status = InstalledCertificate.STATUS_INSTALLED
                installed.installed_at = responded_at
            elif status_value.casefold() == "rejected":
                installed.status = InstalledCertificate.STATUS_REJECTED
            else:
                installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_INSTALL
            installed.save(update_fields=["status", "installed_at", "last_action"])

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"InstallCertificate result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_delete_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            if status_value.casefold() == "accepted":
                installed.status = InstalledCertificate.STATUS_DELETED
                installed.deleted_at = responded_at
            elif status_value.casefold() == "rejected":
                installed.status = InstalledCertificate.STATUS_REJECTED
            else:
                installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_DELETE
            installed.save(update_fields=["status", "deleted_at", "last_action"])

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"DeleteCertificate result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_certificate_signed_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"CertificateSigned result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_get_installed_certificate_ids_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = str(payload_data.get("status") or "").strip() or "Unknown"
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()
    certificates = payload_data.get("certificateHashData") or []

    def _apply():
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            if status_value.casefold() == "accepted":
                operation.status = CertificateOperation.STATUS_ACCEPTED
            elif status_value.casefold() == "rejected":
                operation.status = CertificateOperation.STATUS_REJECTED
            else:
                operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = status_info
            operation.response_payload = payload_data
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        if status_value.casefold() != "accepted":
            return
        charger = Charger.objects.filter(charger_id=charger_id).first()
        if charger is None:
            return
        if isinstance(certificates, dict):
            entries = [certificates]
        elif isinstance(certificates, list):
            entries = certificates
        else:
            entries = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            hash_data = entry.get("hashData") or entry.get("certificateHashData") or entry
            if not isinstance(hash_data, dict):
                continue
            cert_type = str(entry.get("certificateType") or "").strip()
            installed, _created = InstalledCertificate.objects.get_or_create(
                charger=charger,
                certificate_hash_data=hash_data,
                defaults={
                    "certificate_type": cert_type,
                    "status": InstalledCertificate.STATUS_INSTALLED,
                    "last_action": CertificateOperation.ACTION_LIST,
                    "installed_at": responded_at,
                },
            )
            if not _created:
                installed.certificate_type = cert_type or installed.certificate_type
                installed.status = InstalledCertificate.STATUS_INSTALLED
                installed.last_action = CertificateOperation.ACTION_LIST
                if installed.installed_at is None:
                    installed.installed_at = responded_at
                installed.save(
                    update_fields=[
                        "certificate_type",
                        "status",
                        "last_action",
                        "installed_at",
                    ]
                )

    await database_sync_to_async(_apply)()
    store.add_log(
        log_key,
        f"GetInstalledCertificateIds result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )
    return True


async def handle_install_certificate_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_INSTALL
            installed.save(update_fields=["status", "last_action"])

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_delete_certificate_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )
        installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
        if installed:
            installed.status = InstalledCertificate.STATUS_ERROR
            installed.last_action = CertificateOperation.ACTION_DELETE
            installed.save(update_fields=["status", "last_action"])

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_certificate_signed_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True


async def handle_get_installed_certificate_ids_error(
    consumer: CallErrorContext,
    message_id: str,
    metadata: dict,
    error_code: str | None,
    description: str | None,
    details: dict | None,
    log_key: str,
) -> bool:
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        detail_text = (description or "").strip() or _json_details(details)
        if not detail_text:
            detail_text = (error_code or "").strip() or "Error"
        operation = CertificateOperation.objects.filter(pk=operation_pk).first()
        if operation:
            operation.status = CertificateOperation.STATUS_ERROR
            operation.status_info = detail_text
            operation.response_payload = {
                "errorCode": error_code or "",
                "description": description or "",
                "details": details or {},
            }
            operation.responded_at = responded_at
            operation.save(
                update_fields=["status", "status_info", "response_payload", "responded_at"]
            )

    await database_sync_to_async(_apply)()
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        success=False,
        error_code=error_code,
        error_description=description,
        error_details=details,
    )
    return True
