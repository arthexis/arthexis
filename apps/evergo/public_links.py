"""Helpers for generating and validating signed public Evergo URLs."""

from __future__ import annotations

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner

CUSTOMER_SIGNATURE_SALT = "evergo.customer-public-detail"
ARTIFACT_SIGNATURE_SALT = "evergo.customer-artifact-download"
DEFAULT_SIGNATURE_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


def _max_age_seconds() -> int:
    return int(getattr(settings, "EVERGO_PUBLIC_SIGNATURE_MAX_AGE_SECONDS", DEFAULT_SIGNATURE_MAX_AGE_SECONDS))


def build_customer_signature(customer_id: int) -> str:
    """Return a signed value authorizing access to a public customer detail page."""
    return TimestampSigner(salt=CUSTOMER_SIGNATURE_SALT).sign(str(customer_id))


def is_valid_customer_signature(customer_id: int, signature: str) -> bool:
    """Validate that the provided signature is valid for this customer ID."""
    if not signature:
        return False
    try:
        expected = TimestampSigner(salt=CUSTOMER_SIGNATURE_SALT).unsign(signature, max_age=_max_age_seconds())
    except (BadSignature, SignatureExpired):
        return False
    return expected == str(customer_id)


def build_artifact_signature(customer_id: int, artifact_id: int) -> str:
    """Return a signed value authorizing access to a specific customer artifact download."""
    payload = f"{customer_id}:{artifact_id}"
    return TimestampSigner(salt=ARTIFACT_SIGNATURE_SALT).sign(payload)


def is_valid_artifact_signature(customer_id: int, artifact_id: int, signature: str) -> bool:
    """Validate that the signature authorizes this customer/artifact combination."""
    if not signature:
        return False
    try:
        expected = TimestampSigner(salt=ARTIFACT_SIGNATURE_SALT).unsign(signature, max_age=_max_age_seconds())
    except (BadSignature, SignatureExpired):
        return False
    return expected == f"{customer_id}:{artifact_id}"
