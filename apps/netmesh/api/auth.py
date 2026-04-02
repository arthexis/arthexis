"""Authentication helpers for node-scoped Netmesh API endpoints."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from django.http import HttpRequest
from django.utils import timezone

from apps.nodes.models import NodeEnrollment

logger = logging.getLogger("apps.netmesh.auth")


@dataclass(frozen=True)
class EnrollmentPrincipal:
    """Authenticated enrollment principal bound to a specific node and scope."""

    enrollment: NodeEnrollment

    @property
    def node(self):
        return self.enrollment.node

    @property
    def site_id(self):
        return self.enrollment.site_id


def _extract_enrollment_token(request: HttpRequest) -> str:
    """Read enrollment token from Bearer auth or an explicit header."""

    authorization = request.META.get("HTTP_AUTHORIZATION", "")
    if authorization.startswith("Bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.META.get("HTTP_X_ENROLLMENT_TOKEN", "").strip()


def _token_hash_candidates(token: str) -> list[str]:
    if token.startswith(NodeEnrollment.TOKEN_PREFIX):
        legacy_token = token[len(NodeEnrollment.TOKEN_PREFIX) :]
        if legacy_token:
            return [
                NodeEnrollment.hash_token(token),
                NodeEnrollment.hash_token(legacy_token),
            ]
    return [NodeEnrollment.hash_token(token)]


def authenticate_enrollment(
    request: HttpRequest,
    *,
    required_scope: str,
) -> tuple[EnrollmentPrincipal | None, tuple[int, str, str] | None]:
    """Resolve a valid enrollment principal for a node-bound API request."""

    token = _extract_enrollment_token(request)
    if not token:
        logger.warning(
            "Netmesh enrollment denied: token missing",
            extra={"event": "netmesh.enrollment.denied", "code": "enrollment_token_missing"},
        )
        return None, (401, "enrollment_token_missing", "missing enrollment token")

    hashes = _token_hash_candidates(token)
    enrollment = (
        NodeEnrollment.objects.select_related("node", "site", "node__role")
        .filter(token_hash__in=hashes)
        .order_by("-created_at")
        .first()
    )
    if enrollment is None:
        logger.warning(
            "Netmesh enrollment denied: token invalid",
            extra={"event": "netmesh.enrollment.denied", "code": "enrollment_token_invalid"},
        )
        return None, (401, "enrollment_token_invalid", "invalid enrollment token")

    if enrollment.revoked_at is not None or enrollment.status == NodeEnrollment.Status.REVOKED:
        enrollment.last_auth_error_code = "enrollment_token_revoked"
        enrollment.save(update_fields=["last_auth_error_code", "updated_at"])
        logger.warning(
            "Netmesh enrollment denied: token revoked",
            extra={
                "event": "netmesh.enrollment.denied",
                "code": "enrollment_token_revoked",
                "node_id": enrollment.node_id,
                "enrollment_id": enrollment.id,
            },
        )
        return None, (401, "enrollment_token_revoked", "enrollment token revoked")

    if enrollment.is_expired:
        enrollment.last_auth_error_code = "enrollment_token_expired"
        enrollment.save(update_fields=["last_auth_error_code", "updated_at"])
        logger.warning(
            "Netmesh enrollment denied: token expired",
            extra={
                "event": "netmesh.enrollment.denied",
                "code": "enrollment_token_expired",
                "node_id": enrollment.node_id,
                "enrollment_id": enrollment.id,
            },
        )
        return None, (401, "enrollment_token_expired", "enrollment token expired")

    if enrollment.status not in {
        NodeEnrollment.Status.PUBLIC_KEY_SUBMITTED,
        NodeEnrollment.Status.ACTIVE,
    }:
        enrollment.last_auth_error_code = "enrollment_not_active"
        enrollment.save(update_fields=["last_auth_error_code", "updated_at"])
        logger.warning(
            "Netmesh enrollment denied: token not active",
            extra={
                "event": "netmesh.enrollment.denied",
                "code": "enrollment_not_active",
                "node_id": enrollment.node_id,
                "enrollment_id": enrollment.id,
            },
        )
        return None, (403, "enrollment_not_active", "enrollment token is not active")

    if enrollment.node.mesh_enrollment_state != enrollment.node.MeshEnrollmentState.ENROLLED:
        enrollment.last_auth_error_code = "node_not_enrolled"
        enrollment.save(update_fields=["last_auth_error_code", "updated_at"])
        logger.warning(
            "Netmesh enrollment denied: node not enrolled",
            extra={
                "event": "netmesh.enrollment.denied",
                "code": "node_not_enrolled",
                "node_id": enrollment.node_id,
                "enrollment_id": enrollment.id,
            },
        )
        return None, (403, "node_not_enrolled", "node is not enrolled")

    if enrollment.scope != required_scope:
        enrollment.last_auth_error_code = "enrollment_scope_insufficient"
        enrollment.save(update_fields=["last_auth_error_code", "updated_at"])
        logger.warning(
            "Netmesh enrollment denied: insufficient scope",
            extra={
                "event": "netmesh.enrollment.denied",
                "code": "enrollment_scope_insufficient",
                "node_id": enrollment.node_id,
                "enrollment_id": enrollment.id,
                "required_scope": required_scope,
                "enrollment_scope": enrollment.scope,
            },
        )
        return None, (403, "enrollment_scope_insufficient", "enrollment token has insufficient scope")

    enrollment.last_authenticated_at = timezone.now()
    enrollment.last_auth_error_code = ""
    enrollment.save(update_fields=["last_authenticated_at", "last_auth_error_code", "updated_at"])
    logger.info(
        "Netmesh enrollment authenticated",
        extra={
            "event": "netmesh.enrollment.authenticated",
            "node_id": enrollment.node_id,
            "enrollment_id": enrollment.id,
            "scope": enrollment.scope,
        },
    )
    return EnrollmentPrincipal(enrollment=enrollment), None
