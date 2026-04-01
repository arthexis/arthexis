"""Authentication helpers for node-scoped Netmesh API endpoints."""

from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest
from apps.nodes.models import NodeEnrollment


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


def authenticate_enrollment(request: HttpRequest) -> tuple[EnrollmentPrincipal | None, str]:
    """Resolve a valid enrollment principal for a node-bound API request."""

    token = _extract_enrollment_token(request)
    if not token:
        return None, "missing enrollment token"

    token_hash = NodeEnrollment.hash_token(token)
    enrollment = (
        NodeEnrollment.objects.select_related("node", "site", "node__role")
        .filter(token_hash=token_hash)
        .first()
    )
    if enrollment is None:
        return None, "invalid enrollment token"

    if enrollment.status not in {
        NodeEnrollment.Status.PUBLIC_KEY_SUBMITTED,
        NodeEnrollment.Status.ACTIVE,
    }:
        return None, "enrollment token is not active"

    if enrollment.revoked_at is not None or enrollment.status == NodeEnrollment.Status.REVOKED:
        return None, "enrollment token revoked"

    if enrollment.is_expired:
        enrollment.status = NodeEnrollment.Status.EXPIRED
        enrollment.save(update_fields=["status", "updated_at"])
        return None, "enrollment token expired"

    if enrollment.node.mesh_enrollment_state != enrollment.node.MeshEnrollmentState.ENROLLED:
        return None, "node is not enrolled"

    return EnrollmentPrincipal(enrollment=enrollment), ""
