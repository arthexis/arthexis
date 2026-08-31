from __future__ import annotations

from channels.db import database_sync_to_async
from django.utils import timezone

from ... import store
from ...models import CertificateOperation, Charger, InstalledCertificate
from ..common import CallResultContext
from .support import _format_status_info


def _status_value(payload_data: dict) -> str:
    return str(payload_data.get("status") or "").strip() or "Unknown"


def _operation_status(status_value: str) -> str:
    status_key = status_value.casefold()
    if status_key == "accepted":
        return CertificateOperation.STATUS_ACCEPTED
    if status_key == "rejected":
        return CertificateOperation.STATUS_REJECTED
    return CertificateOperation.STATUS_ERROR


def _installed_status(status_value: str, accepted_status: str) -> str:
    status_key = status_value.casefold()
    if status_key == "accepted":
        return accepted_status
    if status_key == "rejected":
        return InstalledCertificate.STATUS_REJECTED
    return InstalledCertificate.STATUS_ERROR


def _apply_operation_response(
    operation_pk: object,
    *,
    status_value: str,
    status_info: str,
    payload_data: dict,
    responded_at,
) -> None:
    operation = CertificateOperation.objects.filter(pk=operation_pk).first()
    if not operation:
        return
    operation.status = _operation_status(status_value)
    operation.status_info = status_info
    operation.response_payload = payload_data
    operation.responded_at = responded_at
    operation.save(
        update_fields=["status", "status_info", "response_payload", "responded_at"]
    )


def _apply_installed_result(
    installed_pk: object,
    *,
    status_value: str,
    accepted_status: str,
    timestamp_field: str,
    action: str,
    responded_at,
) -> None:
    installed = InstalledCertificate.objects.filter(pk=installed_pk).first()
    if not installed:
        return
    installed.status = _installed_status(status_value, accepted_status)
    if status_value.casefold() == "accepted":
        setattr(installed, timestamp_field, responded_at)
    installed.last_action = action
    installed.save(update_fields=["status", timestamp_field, "last_action"])


def _record_certificate_result(
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
    action_name: str,
    status_value: str,
) -> None:
    store.add_log(
        log_key,
        f"{action_name} result: status={status_value}",
        log_type="charger",
    )
    store.record_pending_call_result(
        message_id,
        metadata=metadata,
        payload=payload_data,
    )


async def handle_install_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = _status_value(payload_data)
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        _apply_operation_response(
            operation_pk,
            status_value=status_value,
            status_info=status_info,
            payload_data=payload_data,
            responded_at=responded_at,
        )
        _apply_installed_result(
            installed_pk,
            status_value=status_value,
            accepted_status=InstalledCertificate.STATUS_INSTALLED,
            timestamp_field="installed_at",
            action=CertificateOperation.ACTION_INSTALL,
            responded_at=responded_at,
        )

    await database_sync_to_async(_apply)()
    _record_certificate_result(
        message_id, metadata, payload_data, log_key, "InstallCertificate", status_value
    )
    return True


async def handle_delete_certificate_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = _status_value(payload_data)
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    installed_pk = metadata.get("installed_certificate_pk")
    responded_at = timezone.now()

    def _apply():
        _apply_operation_response(
            operation_pk,
            status_value=status_value,
            status_info=status_info,
            payload_data=payload_data,
            responded_at=responded_at,
        )
        _apply_installed_result(
            installed_pk,
            status_value=status_value,
            accepted_status=InstalledCertificate.STATUS_DELETED,
            timestamp_field="deleted_at",
            action=CertificateOperation.ACTION_DELETE,
            responded_at=responded_at,
        )

    await database_sync_to_async(_apply)()
    _record_certificate_result(
        message_id, metadata, payload_data, log_key, "DeleteCertificate", status_value
    )
    return True


async def handle_certificate_signed_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = _status_value(payload_data)
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    responded_at = timezone.now()

    def _apply():
        _apply_operation_response(
            operation_pk,
            status_value=status_value,
            status_info=status_info,
            payload_data=payload_data,
            responded_at=responded_at,
        )

    await database_sync_to_async(_apply)()
    _record_certificate_result(
        message_id, metadata, payload_data, log_key, "CertificateSigned", status_value
    )
    return True


async def handle_get_installed_certificate_ids_result(
    consumer: CallResultContext,
    message_id: str,
    metadata: dict,
    payload_data: dict,
    log_key: str,
) -> bool:
    status_value = _status_value(payload_data)
    status_info = _format_status_info(payload_data.get("statusInfo"))
    operation_pk = metadata.get("operation_pk")
    charger_id = metadata.get("charger_id")
    responded_at = timezone.now()
    certificates = payload_data.get("certificateHashData") or []

    def _apply():
        _apply_operation_response(
            operation_pk,
            status_value=status_value,
            status_info=status_info,
            payload_data=payload_data,
            responded_at=responded_at,
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
    _record_certificate_result(
        message_id,
        metadata,
        payload_data,
        log_key,
        "GetInstalledCertificateIds",
        status_value,
    )
    return True
