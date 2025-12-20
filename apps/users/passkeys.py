"""Utilities for WebAuthn passkey enrollment and authentication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse

from django.http import HttpRequest
from django.http.request import split_domain_port

from config.request_utils import is_https_request

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response as _verify_authentication_response,
    verify_registration_response as _verify_registration_response,
)
from webauthn.authentication.verify_authentication_response import VerifiedAuthentication
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    PublicKeyCredentialType,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration


@dataclass(slots=True)
class RegistrationOptions:
    """Serialized options returned when starting passkey registration."""

    data: dict
    challenge: str
    user_handle: str


@dataclass(slots=True)
class AuthenticationOptions:
    """Serialized options returned when starting a passkey login."""

    data: dict
    challenge: str


def _rp_id(request: HttpRequest) -> str:
    host = request.get_host()
    domain, _ = split_domain_port(host)
    candidate = domain or host
    return candidate.strip().lower()


def _expected_origins(request: HttpRequest) -> list[str]:
    origins: set[str] = set()
    scheme = "https" if is_https_request(request) else "http"
    host = request.get_host()
    if host:
        origins.add(f"{scheme}://{host}")
    forwarded_host = request.META.get("HTTP_X_FORWARDED_HOST")
    if forwarded_host:
        forwarded_host = forwarded_host.split(",", 1)[0].strip()
        forwarded_proto = request.META.get("HTTP_X_FORWARDED_PROTO", scheme)
        forwarded_proto = forwarded_proto.split(",", 1)[0].strip() or scheme
        if forwarded_host:
            origins.add(f"{forwarded_proto}://{forwarded_host}")
    origin_header = request.META.get("HTTP_ORIGIN")
    if origin_header:
        origins.add(origin_header)
    referer = request.META.get("HTTP_REFERER")
    if referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return sorted(origins)


def build_registration_options(
    request: HttpRequest,
    *,
    user_id: str | bytes,
    user_name: str,
    user_display_name: str,
    rp_name: str,
    exclude_credentials: Sequence[bytes] = (),
) -> RegistrationOptions:
    """Return WebAuthn registration options for the given user."""

    descriptors: list[PublicKeyCredentialDescriptor] = [
        PublicKeyCredentialDescriptor(
            type=PublicKeyCredentialType.PUBLIC_KEY,
            id=credential_id,
        )
        for credential_id in exclude_credentials
    ]
    if isinstance(user_id, (bytes, bytearray)):
        user_id_bytes = bytes(user_id)
        user_id_value = bytes_to_base64url(user_id_bytes)
    else:
        user_id_value = str(user_id)
        try:
            user_id_bytes = base64url_to_bytes(user_id_value)
        except (ValueError, TypeError):
            user_id_bytes = user_id_value.encode("utf-8")
    user_handle = bytes_to_base64url(user_id_bytes)
    selection = AuthenticatorSelectionCriteria(
        resident_key=ResidentKeyRequirement.PREFERRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    options = generate_registration_options(
        rp_id=_rp_id(request),
        rp_name=rp_name,
        user_id=user_id_value,
        user_name=user_name,
        user_display_name=user_display_name,
        authenticator_selection=selection,
        exclude_credentials=descriptors,
    )
    return RegistrationOptions(
        data=json.loads(options_to_json(options)),
        challenge=bytes_to_base64url(options.challenge),
        user_handle=user_handle,
    )


def build_authentication_options(
    request: HttpRequest,
) -> AuthenticationOptions:
    """Return WebAuthn authentication options for the current request."""

    options = generate_authentication_options(
        rp_id=_rp_id(request),
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return AuthenticationOptions(
        data=json.loads(options_to_json(options)),
        challenge=bytes_to_base64url(options.challenge),
    )


def verify_registration_response(
    request: HttpRequest,
    credential: dict,
    *,
    expected_challenge: str,
) -> VerifiedRegistration:
    """Validate a passkey registration response."""

    return _verify_registration_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(expected_challenge),
        expected_rp_id=_rp_id(request),
        expected_origin=_expected_origins(request),
        require_user_verification=True,
    )


def verify_authentication_response(
    request: HttpRequest,
    credential: dict,
    *,
    expected_challenge: str,
    credential_public_key: bytes,
    credential_current_sign_count: int,
) -> VerifiedAuthentication:
    """Validate a passkey authentication response."""

    return _verify_authentication_response(
        credential=credential,
        expected_challenge=base64url_to_bytes(expected_challenge),
        expected_rp_id=_rp_id(request),
        expected_origin=_expected_origins(request),
        credential_public_key=credential_public_key,
        credential_current_sign_count=credential_current_sign_count,
        require_user_verification=True,
    )
