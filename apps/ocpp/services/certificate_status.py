from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from django.conf import settings

from apps.ocpp.models import InstalledCertificate
from apps.ocpp.payload_types import CertificateHashData, OCSPResultPayload


STATE_ACCEPTED = "accepted"
STATE_NOT_FOUND = "not_found"
STATE_REVOKED = "revoked"
STATE_UNKNOWN = "unknown"
STATE_RESPONDER_UNAVAILABLE = "responder_unavailable"
STATE_VALIDATION_ERROR = "validation_error"

_REQUIRED_HASH_FIELDS = (
    "hashAlgorithm",
    "issuerKeyHash",
    "issuerNameHash",
    "serialNumber",
)
_OCSP_STATUS_GOOD = "good"
_OCSP_STATUS_REVOKED = "revoked"
_OCSP_STATUS_UNKNOWN = "unknown"


@dataclass(slots=True)
class CertificateStatusOutcome:
    state: str
    status_info: str = ""
    responder_errors: list[str] = field(default_factory=list)
    ocsp_result: OCSPResultPayload = field(
        default_factory=lambda: _structured_ocsp_result(status="", responder_url="")
    )


def check_certificate_status(
    *, hash_data: CertificateHashData, target
) -> CertificateStatusOutcome:
    validation_error = _validate_hash_data(hash_data)
    if validation_error:
        return CertificateStatusOutcome(
            state=STATE_VALIDATION_ERROR,
            status_info=validation_error,
        )

    installed = (
        InstalledCertificate.objects.filter(
            charger=target,
            certificate_hash_data=hash_data,
        )
        .order_by("-pk")
        .first()
    )
    if not installed or installed.status != InstalledCertificate.STATUS_INSTALLED:
        return CertificateStatusOutcome(
            state=STATE_NOT_FOUND,
            status_info="Certificate not found.",
        )

    chain_valid, chain_error = _validate_chain(installed.certificate)
    if not chain_valid:
        return CertificateStatusOutcome(
            state=STATE_VALIDATION_ERROR,
            status_info=chain_error or "Certificate chain validation failed.",
        )

    ocsp_data, ocsp_error = _check_ocsp(hash_data)
    if ocsp_error:
        fail_closed = bool(getattr(settings, "OCPP_CERT_STATUS_FAIL_CLOSED", False))
        return CertificateStatusOutcome(
            state=STATE_RESPONDER_UNAVAILABLE if fail_closed else STATE_ACCEPTED,
            status_info=ocsp_error,
            responder_errors=[ocsp_error],
            ocsp_result=ocsp_data,
        )

    ocsp_status = str(ocsp_data.get("status") or "").strip().lower()
    if ocsp_status == _OCSP_STATUS_REVOKED:
        return CertificateStatusOutcome(
            state=STATE_REVOKED,
            status_info="Certificate has been revoked.",
            ocsp_result=ocsp_data,
        )
    if ocsp_status == _OCSP_STATUS_UNKNOWN:
        return CertificateStatusOutcome(
            state=STATE_UNKNOWN,
            status_info="Certificate revocation status is unknown.",
            ocsp_result=ocsp_data,
        )

    if ocsp_status not in {"", _OCSP_STATUS_GOOD}:
        return CertificateStatusOutcome(
            state=STATE_VALIDATION_ERROR,
            status_info=f"Unsupported OCSP status '{ocsp_status}'.",
            ocsp_result=ocsp_data,
        )

    crl_revoked, crl_error = _check_crl(hash_data)
    if crl_error:
        fail_closed = bool(getattr(settings, "OCPP_CERT_STATUS_FAIL_CLOSED", False))
        return CertificateStatusOutcome(
            state=STATE_RESPONDER_UNAVAILABLE if fail_closed else STATE_ACCEPTED,
            status_info=crl_error,
            responder_errors=[crl_error],
            ocsp_result=ocsp_data,
        )
    if crl_revoked:
        return CertificateStatusOutcome(
            state=STATE_REVOKED,
            status_info="Certificate has been revoked.",
            ocsp_result=ocsp_data,
        )

    return CertificateStatusOutcome(
        state=STATE_ACCEPTED,
        ocsp_result=ocsp_data,
    )


def _check_ocsp(hash_data: CertificateHashData) -> tuple[OCSPResultPayload, str]:
    configured_url = str(getattr(settings, "OCPP_CERT_STATUS_OCSP_URL", "") or "").strip()
    if not configured_url:
        return _structured_ocsp_result(status=_OCSP_STATUS_GOOD, responder_url=""), ""

    response_json, error = _request_with_retry(
        method="post",
        url=configured_url,
        payload={"certificateHashData": hash_data},
    )
    if error:
        return (
            _structured_ocsp_result(
                status=_OCSP_STATUS_UNKNOWN,
                responder_url=configured_url,
                errors=[error],
            ),
            f"OCSP responder unavailable: {error}",
        )

    status = str(response_json.get("status") or _OCSP_STATUS_UNKNOWN).strip().lower()
    responder_url = (
        str(response_json.get("responderUrl") or response_json.get("responderURL") or "").strip()
        or configured_url
    )
    errors = response_json.get("errors")
    if isinstance(errors, list):
        error_values = [str(value) for value in errors if str(value).strip()]
    else:
        error_values = []

    return (
        _structured_ocsp_result(
            status=status,
            responder_url=responder_url,
            produced_at=response_json.get("producedAt"),
            this_update=response_json.get("thisUpdate"),
            next_update=response_json.get("nextUpdate"),
            errors=error_values,
        ),
        "",
    )


def _check_crl(hash_data: CertificateHashData) -> tuple[bool, str]:
    configured_url = str(getattr(settings, "OCPP_CERT_STATUS_CRL_URL", "") or "").strip()
    if not configured_url:
        return False, ""

    response_json, error = _request_with_retry(
        method="get",
        url=configured_url,
        payload={"serialNumber": hash_data.get("serialNumber")},
    )
    if error:
        return False, f"CRL responder unavailable: {error}"

    serial_number = str(hash_data.get("serialNumber") or "").strip()
    revoked_serials = response_json.get("revokedSerialNumbers")
    if not isinstance(revoked_serials, list):
        return False, "CRL responder returned invalid payload."
    normalized_serials = {str(value).strip() for value in revoked_serials}
    return serial_number in normalized_serials, ""


def _request_with_retry(
    *,
    method: str,
    url: str,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    try:
        timeout_seconds = int(getattr(settings, "OCPP_CERT_STATUS_TIMEOUT_SECONDS", 3))
    except (TypeError, ValueError):
        timeout_seconds = 3
    if timeout_seconds <= 0:
        timeout_seconds = 3

    try:
        max_retries = int(getattr(settings, "OCPP_CERT_STATUS_RETRIES", 1))
    except (TypeError, ValueError):
        max_retries = 1
    if max_retries < 0:
        max_retries = 0

    method_name = method.strip().lower()
    last_error = "No response."
    attempts = max_retries + 1

    for _ in range(attempts):
        try:
            if method_name == "post":
                response = requests.post(url, json=payload, timeout=timeout_seconds)
            elif method_name == "get":
                response = requests.get(url, params=payload, timeout=timeout_seconds)
            else:
                return {}, f"Unsupported method '{method_name}'."
        except requests.Timeout:
            last_error = "Request timed out."
            continue
        except requests.RequestException as exc:
            last_error = str(exc) or "Responder request failed."
            continue

        try:
            data = response.json()
        except ValueError:
            return {}, "Responder returned invalid JSON."

        if response.status_code >= 400:
            error_detail = data.get("error") if isinstance(data, dict) else ""
            last_error = str(error_detail).strip() or f"HTTP {response.status_code}"
            continue

        if isinstance(data, dict):
            return data, ""
        return {}, "Responder returned invalid payload."

    return {}, last_error


def _structured_ocsp_result(
    *,
    status: str,
    responder_url: str,
    produced_at: Any = None,
    this_update: Any = None,
    next_update: Any = None,
    errors: list[str] | None = None,
) -> OCSPResultPayload:
    return {
        "status": status,
        "responderUrl": responder_url,
        "producedAt": _iso_datetime_or_now(produced_at),
        "thisUpdate": _iso_datetime_or_now(this_update),
        "nextUpdate": _iso_datetime_or_now(next_update),
        "errors": errors or [],
    }


def _iso_datetime_or_now(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return datetime.now(tz=timezone.utc).isoformat()


def _validate_chain(certificate_chain: str) -> tuple[bool, str]:
    trust_store_path = str(getattr(settings, "OCPP_CERT_STATUS_TRUST_STORE", "") or "").strip()
    if not trust_store_path:
        return True, ""
    if not certificate_chain.strip():
        return False, "Certificate chain is missing."
    return True, ""


def _validate_hash_data(hash_data: CertificateHashData) -> str:
    if not isinstance(hash_data, dict):
        return "certificateHashData must be an object."
    missing_fields = [
        field_name
        for field_name in _REQUIRED_HASH_FIELDS
        if not str(hash_data.get(field_name) or "").strip()
    ]
    if missing_fields:
        fields = ", ".join(sorted(missing_fields))
        return f"Malformed certificate hash data: missing {fields}."
    return ""
