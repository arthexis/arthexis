from __future__ import annotations

from base64 import b64decode
from dataclasses import dataclass, field

from cryptography import x509

SUPPORTED_CERTIFICATE_TYPES = frozenset(
    {
        "ChargingStationCertificate",
        "CSMSCertificate",
        "ManufacturerRootCertificate",
        "V2G",
        "V2GCertificate",
        "V2GRootCertificate",
    }
)

REASON_FORMAT_VIOLATION = "FormatViolation"
REASON_INVALID_SIGNATURE = "InvalidSignature"
REASON_UNSUPPORTED_CERTIFICATE_TYPE = "UnsupportedCertificateType"


@dataclass(slots=True)
class CertificateValidationResult:
    valid: bool
    reason_code: str = ""
    details: dict[str, str] = field(default_factory=dict)


def validate_certificate_type(certificate_type: str) -> CertificateValidationResult:
    value = str(certificate_type or "").strip()
    if not value:
        return CertificateValidationResult(
            valid=False,
            reason_code=REASON_FORMAT_VIOLATION,
            details={"message": "certificateType is required."},
        )
    if value not in SUPPORTED_CERTIFICATE_TYPES:
        return CertificateValidationResult(
            valid=False,
            reason_code=REASON_UNSUPPORTED_CERTIFICATE_TYPE,
            details={"message": f"Unsupported certificate type '{value}'."},
        )
    return CertificateValidationResult(valid=True)


def validate_optional_certificate_type(
    certificate_type: str,
) -> CertificateValidationResult:
    value = str(certificate_type or "").strip()
    if not value:
        return CertificateValidationResult(valid=True)
    return validate_certificate_type(value)


def validate_csr_payload(
    value: str, *, payload_name: str
) -> CertificateValidationResult:
    payload = str(value or "").strip()
    if not payload:
        return CertificateValidationResult(
            valid=False,
            reason_code=REASON_FORMAT_VIOLATION,
            details={"message": f"{payload_name} payload is missing."},
        )

    parse_result = _parse_csr(payload)
    if not parse_result.valid:
        return parse_result

    return CertificateValidationResult(valid=True)


def _parse_csr(value: str) -> CertificateValidationResult:
    text = value.strip()
    loaders: list[tuple[str, bytes]] = []

    if "BEGIN CERTIFICATE REQUEST" in text:
        loaders.append(("pem", text.encode()))
    else:
        try:
            decoded = b64decode(text, validate=True)
        except ValueError:
            decoded = b""
        if decoded:
            loaders.append(("der", decoded))
            if b"BEGIN CERTIFICATE REQUEST" in decoded:
                loaders.append(("pem", decoded))
        loaders.append(("der", text.encode()))

    for encoding, candidate in loaders:
        try:
            if encoding == "pem":
                csr = x509.load_pem_x509_csr(candidate)
            else:
                csr = x509.load_der_x509_csr(candidate)
        except ValueError:
            continue

        if not csr.is_signature_valid:
            return CertificateValidationResult(
                valid=False,
                reason_code=REASON_INVALID_SIGNATURE,
                details={"message": "CSR signature is invalid."},
            )
        if not csr.subject:
            return CertificateValidationResult(
                valid=False,
                reason_code=REASON_FORMAT_VIOLATION,
                details={"message": "CSR subject is missing."},
            )
        return CertificateValidationResult(valid=True)

    return CertificateValidationResult(
        valid=False,
        reason_code=REASON_FORMAT_VIOLATION,
        details={"message": "CSR payload is not a valid PKCS#10 request."},
    )


__all__ = [
    "CertificateValidationResult",
    "REASON_FORMAT_VIOLATION",
    "REASON_INVALID_SIGNATURE",
    "REASON_UNSUPPORTED_CERTIFICATE_TYPE",
    "SUPPORTED_CERTIFICATE_TYPES",
    "validate_certificate_type",
    "validate_optional_certificate_type",
    "validate_csr_payload",
]
