"""Authentication and signature verification for registration endpoints."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth import authenticate
from django.http import JsonResponse

from .policy import allow_authenticated_signature_fallback


def _authenticate_basic_credentials(request):
    """Authenticate request ``Authorization: Basic`` credentials if present."""

    header = request.META.get("HTTP_AUTHORIZATION", "")
    if not header.startswith("Basic "):
        return None
    try:
        encoded = header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return None
    user = authenticate(request=request, username=username, password=password)
    if user is not None:
        request.user = user
        request._cached_user = user
    return user


def _verify_signature(payload):
    """Verify payload token signature and return ``(verified, error_response)``."""

    if not (payload.public_key and payload.token and payload.signature):
        return False, None
    try:
        pub = serialization.load_pem_public_key(payload.public_key.encode())
        pub.verify(
            base64.b64decode(payload.signature),
            payload.token.encode(),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True, None
    except Exception:
        return False, JsonResponse({"detail": "invalid signature"}, status=403)


def _enforce_authentication(request, *, verified: bool):
    """Return auth error response if registration should be denied."""

    if verified:
        return None
    user = getattr(request, "user", AnonymousUser())
    if not user.is_authenticated:
        return JsonResponse({"detail": "authentication required"}, status=401)
    required_perms = ("nodes.add_node", "nodes.change_node")
    if not user.has_perms(required_perms):
        return JsonResponse({"detail": "permission denied"}, status=403)
    return None


def allow_signature_failure_with_authenticated_user(request, signature_error) -> bool:
    """Return whether an authenticated user may proceed after signature failure."""

    user = getattr(request, "user", AnonymousUser())
    return bool(
        signature_error
        and user.is_authenticated
        and allow_authenticated_signature_fallback()
    )


def ensure_authenticated_user(request):
    """Ensure ``request.user`` is authenticated via session or Basic auth."""

    authenticated_user = getattr(request, "user", None)
    if not getattr(authenticated_user, "is_authenticated", False):
        authenticated_user = _authenticate_basic_credentials(request)
    return authenticated_user
