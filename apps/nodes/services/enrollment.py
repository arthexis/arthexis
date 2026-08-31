from __future__ import annotations

from datetime import timedelta

from django.contrib.sites.models import Site
from django.db import transaction
from django.utils import timezone

from apps.nodes.models import Node, NodeEnrollment, NodeEnrollmentEvent


def _normalize_scope(scope: str) -> str:
    normalized_scope = scope.strip()
    if normalized_scope not in NodeEnrollment.ALLOWED_SCOPES:
        raise ValueError(f"Unsupported enrollment scope: {scope!r}")
    return normalized_scope


def _record_event(*, node: Node, enrollment: NodeEnrollment | None, action: str, actor=None, from_state: str = "", to_state: str = "", details: dict | None = None):
    NodeEnrollmentEvent.objects.create(
        node=node,
        enrollment=enrollment,
        action=action,
        actor=actor,
        from_state=from_state,
        to_state=to_state,
        details=details or {},
    )


@transaction.atomic
def issue_enrollment_token(
    *,
    node: Node,
    actor=None,
    site: Site | None = None,
    reissue: bool = False,
    scope: str = "mesh:read",
    ttl: timedelta = timedelta(hours=1),
):
    normalized_scope = _normalize_scope(scope)
    current_state = node.mesh_enrollment_state
    node.mesh_enrollment_state = Node.MeshEnrollmentState.PENDING
    node.save(update_fields=["mesh_enrollment_state"])

    if reissue:
        now = timezone.now()
        node.enrollments.exclude(status=NodeEnrollment.Status.REVOKED).filter(
            revoked_at__isnull=True,
            used_at__isnull=True,
            expires_at__gt=now,
        ).update(
            status=NodeEnrollment.Status.REVOKED,
            revoked_at=now,
            updated_at=now,
        )

    enrollment, token = NodeEnrollment.issue(
        node=node,
        site=site or node.base_site,
        issued_by=actor,
        scope=normalized_scope,
        ttl=ttl,
    )
    _record_event(
        node=node,
        enrollment=enrollment,
        action=(NodeEnrollmentEvent.Action.TOKEN_REISSUED if reissue else NodeEnrollmentEvent.Action.TOKEN_ISSUED),
        actor=actor,
        from_state=current_state,
        to_state=node.mesh_enrollment_state,
        details={"site_id": enrollment.site_id, "token_hint": enrollment.token_hint, "scope": enrollment.scope},
    )
    return enrollment, token


def submit_public_key(*, node: Node, token: str, public_key: str, site: Site | None = None):
    token_hash = NodeEnrollment.hash_token(token)
    with transaction.atomic():
        enrollment = (
            NodeEnrollment.objects.select_for_update()
            .filter(node=node, token_hash=token_hash)
            .order_by("-created_at")
            .first()
        )
        if enrollment is None:
            return None, "Invalid enrollment token"
        if enrollment.status == NodeEnrollment.Status.REVOKED or enrollment.revoked_at:
            return None, "Enrollment token revoked"
        if enrollment.is_expired:
            enrollment.status = NodeEnrollment.Status.EXPIRED
            enrollment.save(update_fields=["status", "updated_at"])
            return None, "Enrollment token expired"
        if enrollment.used_at is not None:
            return None, "Enrollment token already used"
        if enrollment.site_id and (not site or enrollment.site_id != site.id):
            return None, "Enrollment token does not match target site"

        current_state = node.mesh_enrollment_state
        old_key = node.public_key or ""
        node.public_key = public_key
        node.mesh_enrollment_state = Node.MeshEnrollmentState.PENDING
        node.save(update_fields=["public_key", "mesh_enrollment_state"])

        enrollment.status = NodeEnrollment.Status.PUBLIC_KEY_SUBMITTED
        enrollment.used_at = timezone.now()
        enrollment.save(update_fields=["status", "used_at", "updated_at"])
    _record_event(
        node=node,
        enrollment=enrollment,
        action=(NodeEnrollmentEvent.Action.KEY_ROTATED if old_key and old_key != public_key else NodeEnrollmentEvent.Action.PUBLIC_KEY_SUBMITTED),
        from_state=current_state,
        to_state=node.mesh_enrollment_state,
        details={"site_id": site.id if site else enrollment.site_id},
    )
    return enrollment, ""


@transaction.atomic
def approve_enrollment(*, node: Node, actor=None):
    current_state = node.mesh_enrollment_state
    node.mesh_enrollment_state = Node.MeshEnrollmentState.ENROLLED
    node.save(update_fields=["mesh_enrollment_state"])

    enrollment = node.enrollments.filter(status=NodeEnrollment.Status.PUBLIC_KEY_SUBMITTED).order_by("-created_at").first()
    if enrollment:
        enrollment.status = NodeEnrollment.Status.ACTIVE
        enrollment.save(update_fields=["status", "updated_at"])
    _record_event(
        node=node,
        enrollment=enrollment,
        action=NodeEnrollmentEvent.Action.APPROVED,
        actor=actor,
        from_state=current_state,
        to_state=node.mesh_enrollment_state,
    )


@transaction.atomic
def revoke_enrollment(*, node: Node, actor=None, reason: str = ""):
    current_state = node.mesh_enrollment_state
    node.mesh_enrollment_state = Node.MeshEnrollmentState.UNENROLLED
    node.mesh_key_fingerprint_metadata = {}
    node.last_mesh_heartbeat = None
    node.mesh_capability_flags = []
    node.save(
        update_fields=[
            "mesh_enrollment_state",
            "mesh_key_fingerprint_metadata",
            "last_mesh_heartbeat",
            "mesh_capability_flags",
        ]
    )

    latest = node.enrollments.order_by("-created_at").first()
    if latest:
        latest.status = NodeEnrollment.Status.REVOKED
        latest.revoked_at = timezone.now()
        latest.save(update_fields=["status", "revoked_at", "updated_at"])
    _record_event(
        node=node,
        enrollment=latest,
        action=NodeEnrollmentEvent.Action.REVOKED,
        actor=actor,
        from_state=current_state,
        to_state=node.mesh_enrollment_state,
        details={"reason": reason},
    )
