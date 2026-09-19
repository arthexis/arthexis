"""Protocol-operation lifecycle services."""

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
