"""OCPP connection negotiation, registration, and authentication."""

import base64
import binascii

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ocpp.models import Charger
from apps.ocpp.protocol.contracts import ProtocolVersion

SUBPROTOCOL_VERSIONS = {
    "ocpp1.6": ProtocolVersion.OCPP_16,
    "ocpp2.0.1": ProtocolVersion.OCPP_201,
}


class ConnectionRejected(ValueError):
    """The client did not offer a retained OCPP subprotocol."""


def negotiate_subprotocol(requested: list[str]) -> tuple[str, ProtocolVersion]:
    """Select the first client-offered retained OCPP subprotocol."""
    for protocol in requested:
        version = SUBPROTOCOL_VERSIONS.get(protocol)
        if version is not None:
            return protocol, version
    raise ConnectionRejected("No supported OCPP subprotocol was offered.")


def basic_credentials(headers: list[tuple[bytes, bytes]]) -> tuple[str, str] | None:
    """Extract Basic credentials without retaining the raw authorization header."""
    for name, value in headers:
        if name.lower() != b"authorization":
            continue
        scheme, _, encoded = value.partition(b" ")
        if scheme.lower() != b"basic" or not encoded:
            return None
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        username, separator, password = decoded.partition(":")
        if not separator or not username or not password:
            return None
        return username, password
    return None


@sync_to_async
def load_or_enroll_charger(
    identity: str, credentials: tuple[str, str] | None
) -> Charger | None:
    """Authenticate an active charger or enroll an unknown identity once."""
    charger = Charger.objects.filter(identity=identity).first()
    if charger is not None:
        if charger.active and authenticate_charger_sync(charger, credentials):
            return charger
        return None
    if not _valid_enrollment_credentials(identity, credentials):
        return None
    try:
        with transaction.atomic():
            return Charger.objects.create(
                identity=identity,
                connection_token_hash=make_password(credentials[1]),
                enrolled_at=timezone.now(),
            )
    except IntegrityError:
        charger = Charger.objects.filter(identity=identity, active=True).first()
        if charger is None or not authenticate_charger_sync(charger, credentials):
            return None
        return charger


def _valid_enrollment_credentials(
    identity: str, credentials: tuple[str, str] | None
) -> bool:
    enrollment_hash = settings.OCPP_ENROLLMENT_TOKEN_HASH
    if credentials is None or not enrollment_hash:
        return False
    credential_identity, token = credentials
    return credential_identity == identity and check_password(token, enrollment_hash)


def authenticate_charger_sync(
    charger: Charger, credentials: tuple[str, str] | None
) -> bool:
    """Verify credentials inside a synchronous enrollment race recovery path."""
    if credentials is None or not charger.connection_token_hash:
        return False
    identity, token = credentials
    return identity == charger.identity and check_password(
        token, charger.connection_token_hash
    )
