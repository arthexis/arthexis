from __future__ import annotations

import base64
import hashlib
import hmac
import json

from django.conf import settings

from apps.cards.soul import PACKAGE_MAX_BYTES

from .survey import digest_normalized_answers, normalize_survey_response

SOUL_ID_HMAC_KEY = getattr(settings, "SOUL_ID_HMAC_KEY", settings.SECRET_KEY)


def _compute_soul_id(*, core_hash: str, survey_digest: str, issuance_marker: str) -> str:
    material = "|".join([core_hash, survey_digest, issuance_marker])
    digest = hmac.new(
        SOUL_ID_HMAC_KEY.encode("utf-8"),
        material.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=").lower()


def build_soul_package(*, registration_session, user) -> tuple[dict, str, str, str]:
    offering = registration_session.offering_soul.package
    normalized = normalize_survey_response(registration_session.survey_response)
    survey_digest = digest_normalized_answers(normalized)
    soul_id = _compute_soul_id(
        core_hash=offering.get("core_hash", ""),
        survey_digest=survey_digest,
        issuance_marker=offering.get("issuance_marker", ""),
    )
    email_hash = hashlib.sha256(user.email.lower().encode("utf-8")).hexdigest()

    package = {
        "schema_version": "1.0",
        "soul_id": soul_id,
        "offering": offering,
        "survey": {
            "survey_id": registration_session.survey_response.survey_id,
            "answers_normalized": normalized,
            "survey_digest": survey_digest,
        },
        "identity": {
            "email_hash": email_hash,
            "email_verified_at": registration_session.verification_sent_at.isoformat()
            if registration_session.verification_sent_at
            else None,
        },
        "security": {
            "sig_alg": "none",
            "kid": "",
            "signature": "",
        },
    }
    encoded = json.dumps(package, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > PACKAGE_MAX_BYTES:
        raise ValueError("Soul package exceeds 512 KB limit.")

    return package, soul_id, survey_digest, email_hash
