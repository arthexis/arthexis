"""Protocol-operation lifecycle services."""

from django.db import transaction
from django.utils import timezone

from apps.ocpp.models import Charger, ProtocolOperation
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.recovery import recovery_policy_for
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
        recovery_policy=recovery_policy_for(version=version, action=action),
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



@transaction.atomic
def prepare_safe_retry(operation: ProtocolOperation) -> ProtocolOperation | None:
    """Return an ambiguous SAFE_RETRY operation to pending for a new attempt."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status != ProtocolOperation.Status.RECOVERY_REQUIRED:
        return None
    if current.recovery_policy != ProtocolOperation.RecoveryPolicy.SAFE_RETRY:
        return None
    current.status = ProtocolOperation.Status.PENDING
    current.completed_at = None
    current.save(update_fields=("status", "completed_at"))
    return current



@transaction.atomic
def prepare_reconnect_operations(
    *,
    charger: Charger,
    version: ProtocolVersion,
) -> tuple[int, ...]:
    """Return durable operation ids that a fresh owning consumer may send.

    A row left DELIVERING by a dead consumer is ambiguous. Convert it to
    RECOVERY_REQUIRED first, then automatically reopen it only when its
    persisted policy is SAFE_RETRY. Existing PENDING work is known-unsent and
    remains eligible regardless of recovery policy.
    """
    operations = list(
        ProtocolOperation.objects.select_for_update()
        .filter(
            charger=charger,
            version=version.value,
            direction=Direction.CSMS_TO_CHARGE_POINT.value,
            status__in=(
                ProtocolOperation.Status.PENDING,
                ProtocolOperation.Status.DELIVERING,
                ProtocolOperation.Status.RECOVERY_REQUIRED,
            ),
        )
        .order_by("created_at", "pk")
    )

    pending_ids: list[int] = []
    for operation in operations:
        if operation.status == ProtocolOperation.Status.DELIVERING:
            operation.status = ProtocolOperation.Status.RECOVERY_REQUIRED
            operation.last_delivery_error = (
                "Previous consumer ended before the outbound outcome was durable."
            )
            operation.completed_at = None
            operation.save(
                update_fields=("status", "last_delivery_error", "completed_at")
            )

        if (
            operation.status == ProtocolOperation.Status.RECOVERY_REQUIRED
            and operation.recovery_policy
            == ProtocolOperation.RecoveryPolicy.SAFE_RETRY
        ):
            operation.status = ProtocolOperation.Status.PENDING
            operation.completed_at = None
            operation.save(update_fields=("status", "completed_at"))

        if operation.status == ProtocolOperation.Status.PENDING:
            pending_ids.append(operation.pk)

    return tuple(pending_ids)
