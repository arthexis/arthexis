"""Protocol-operation lifecycle services."""

from django.db import transaction
from django.utils import timezone

from apps.ocpp.models import Charger, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.registry import resolve_action


def create_operation(
    *,
    charger: Charger,
    version: ProtocolVersion,
    direction: Direction,
    action: str,
    request_payload: dict[str, object],
) -> ProtocolOperation:
    """Create one matrix-approved protocol operation in the pending state."""
    if resolve_action(version=version, direction=direction, action=action) is None:
        raise ValueError(f"Unsupported OCPP operation: {version} {direction} {action}")
    return ProtocolOperation.objects.create(
        charger=charger,
        version=version,
        direction=direction,
        action=action,
        request_payload=request_payload,
    )


def complete_operation(
    operation: ProtocolOperation,
    *,
    response_payload: dict[str, object] | None = None,
    error_code: str = "",
    error_description: str = "",
) -> ProtocolOperation:
    """Store a terminal response or error without sending a charger command."""
    operation.response_payload = response_payload
    operation.error_code = error_code
    operation.error_description = error_description
    operation.status = (
        ProtocolOperation.Status.ERRORED
        if error_code
        else ProtocolOperation.Status.COMPLETED
    )
    operation.completed_at = timezone.now()
    operation.save(
        update_fields=(
            "response_payload",
            "error_code",
            "error_description",
            "status",
            "completed_at",
        )
    )
    return operation



@transaction.atomic
def claim_operation(operation: ProtocolOperation) -> ProtocolOperation | None:
    """Claim one pending operation immediately before an actual charger send."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status != ProtocolOperation.Status.PENDING:
        return None
    now = timezone.now()
    current.status = ProtocolOperation.Status.DELIVERING
    current.attempt_count += 1
    current.first_attempt_at = current.first_attempt_at or now
    current.last_attempt_at = now
    current.last_delivery_error = ""
    current.save(
        update_fields=(
            "status",
            "attempt_count",
            "first_attempt_at",
            "last_attempt_at",
            "last_delivery_error",
        )
    )
    return current


@transaction.atomic
def retain_pending_operation(
    operation: ProtocolOperation,
    *,
    description: str,
) -> ProtocolOperation:
    """Retain known-unsent intent without overwriting a concurrent delivery claim."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status == ProtocolOperation.Status.PENDING:
        current.last_delivery_error = description[:240]
        current.save(update_fields=("last_delivery_error",))
    return current


@transaction.atomic
def require_operation_recovery(
    operation: ProtocolOperation,
    *,
    description: str,
) -> ProtocolOperation:
    """Preserve an ambiguous charger send for later action-specific reconciliation."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status == ProtocolOperation.Status.DELIVERING:
        current.status = ProtocolOperation.Status.RECOVERY_REQUIRED
        current.last_delivery_error = description[:240]
        current.completed_at = None
        current.save(
            update_fields=("status", "last_delivery_error", "completed_at")
        )
    return current
