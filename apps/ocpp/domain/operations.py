"""Protocol-operation lifecycle services."""

import uuid

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from apps.ocpp.models import Charger, OcppTransaction, ProtocolOperation
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
def claim_operation(
    operation: ProtocolOperation,
    *,
    delivery_owner: str = "",
) -> ProtocolOperation | None:
    """Claim one pending operation immediately before an actual charger send."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status != ProtocolOperation.Status.PENDING:
        return None
    now = timezone.now()
    current.status = ProtocolOperation.Status.DELIVERING
    current.attempt_token = uuid.uuid4()
    current.delivery_owner = delivery_owner
    current.attempt_count += 1
    current.first_attempt_at = current.first_attempt_at or now
    current.last_attempt_at = now
    current.last_delivery_error = ""
    current.save(
        update_fields=(
            "status",
            "attempt_token",
            "delivery_owner",
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
    attempt_token: uuid.UUID | None = None,
) -> ProtocolOperation:
    """Preserve an ambiguous charger send for later action-specific reconciliation."""
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if (
        current.status == ProtocolOperation.Status.DELIVERING
        and (attempt_token is None or current.attempt_token == attempt_token)
    ):
        current.status = ProtocolOperation.Status.RECOVERY_REQUIRED
        current.last_delivery_error = description[:240]
        current.completed_at = None
        current.save(
            update_fields=("status", "last_delivery_error", "completed_at")
        )
    return current


SESSION_RECONCILE_ACTIONS = frozenset(
    {
        "RemoteStartTransaction",
        "RequestStartTransaction",
        "RemoteStopTransaction",
        "RequestStopTransaction",
    }
)


def _matching_remote_start_transaction(
    operation: ProtocolOperation,
) -> OcppTransaction | None:
    attempt_at = operation.last_attempt_at or operation.first_attempt_at
    if attempt_at is None:
        return None

    queryset = OcppTransaction.objects.filter(
        charger=operation.charger,
        historical=False,
        started_at__gte=attempt_at,
    )

    payload = operation.request_payload
    if operation.action == "RemoteStartTransaction":
        id_tag = payload.get("idTag")
        if not isinstance(id_tag, str) or not id_tag:
            return None
        queryset = queryset.filter(id_tag=id_tag)
        connector_id = payload.get("connectorId")
        if connector_id is not None:
            if not isinstance(connector_id, int):
                return None
            if connector_id > 0:
                queryset = queryset.filter(connector__number=connector_id)
    else:
        id_token = payload.get("idToken")
        if not isinstance(id_token, dict):
            return None
        token = id_token.get("idToken")
        if not isinstance(token, str) or not token or len(token) > 20:
            return None
        queryset = queryset.filter(id_tag=token)
        evse_id = payload.get("evseId")
        if evse_id is not None:
            if not isinstance(evse_id, int):
                return None
            queryset = queryset.filter(
                connector__number__gte=evse_id * 1000,
                connector__number__lt=(evse_id + 1) * 1000,
            )

    return queryset.order_by("started_at", "pk").first()


def _remote_stop_transaction(operation: ProtocolOperation) -> OcppTransaction | None:
    transaction_id = operation.request_payload.get("transactionId")
    if operation.action == "RemoteStopTransaction":
        if isinstance(transaction_id, int):
            return OcppTransaction.objects.filter(
                charger=operation.charger,
                historical=False,
                pk=transaction_id,
            ).first()
        if isinstance(transaction_id, str):
            return OcppTransaction.objects.filter(
                charger=operation.charger,
                historical=False,
                remote_id=transaction_id,
            ).first()
        return None

    if not isinstance(transaction_id, str) or not transaction_id:
        return None
    return OcppTransaction.objects.filter(
        charger=operation.charger,
        historical=False,
        remote_id=transaction_id,
    ).first()


def _settle_reconciled_operation(
    operation: ProtocolOperation,
    *,
    basis: str,
) -> ProtocolOperation:
    now = timezone.now()
    operation.status = ProtocolOperation.Status.COMPLETED
    operation.completed_at = now
    operation.reconciliation_checked_at = now
    operation.reconciled_at = now
    operation.reconciliation_resolution = (
        ProtocolOperation.ReconciliationResolution.ACHIEVED
    )
    operation.reconciliation_basis = basis[:240]
    operation.save(
        update_fields=(
            "status",
            "completed_at",
            "reconciliation_checked_at",
            "reconciled_at",
            "reconciliation_resolution",
            "reconciliation_basis",
        )
    )
    return operation


@transaction.atomic
def reconcile_session_operation(operation: ProtocolOperation) -> ProtocolOperation:
    """Resolve ambiguous remote start/stop work from positive retained session evidence."""
    current = (
        ProtocolOperation.objects.select_for_update()
        .select_related("charger")
        .get(pk=operation.pk)
    )
    if current.status != ProtocolOperation.Status.RECOVERY_REQUIRED:
        return current
    if current.recovery_policy != ProtocolOperation.RecoveryPolicy.RECONCILE:
        return current
    if current.action not in SESSION_RECONCILE_ACTIONS:
        return current

    current.reconciliation_checked_at = timezone.now()
    current.save(update_fields=("reconciliation_checked_at",))

    if current.action in {"RemoteStartTransaction", "RequestStartTransaction"}:
        matched = _matching_remote_start_transaction(current)
        if matched is None:
            return current
        return _settle_reconciled_operation(
            current,
            basis=(
                f"Retained transaction {matched.remote_id} started after the ambiguous "
                f"{current.action} attempt with matching requested session identity."
            ),
        )

    target = _remote_stop_transaction(current)
    if target is None or target.stopped_at is None:
        return current
    return _settle_reconciled_operation(
        current,
        basis=(
            f"Retained transaction {target.remote_id} is durably completed "
            f"after ambiguous {current.action} intent."
        ),
    )


def reconcile_session_operations(*, limit: int = 100) -> int:
    """Reconcile one fair bounded batch of ambiguous remote start/stop operations."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    operation_ids = tuple(
        ProtocolOperation.objects.filter(
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            recovery_policy=ProtocolOperation.RecoveryPolicy.RECONCILE,
            action__in=SESSION_RECONCILE_ACTIONS,
        )
        .order_by(F("reconciliation_checked_at").asc(nulls_first=True), "pk")
        .values_list("pk", flat=True)[:limit]
    )
    resolved = 0
    for operation_id in operation_ids:
        current = reconcile_session_operation(ProtocolOperation(pk=operation_id))
        if (
            current.status == ProtocolOperation.Status.COMPLETED
            and current.reconciled_at is not None
        ):
            resolved += 1
    return resolved


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
    delivery_owner: str,
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
        if (
            operation.status == ProtocolOperation.Status.DELIVERING
            and operation.delivery_owner != delivery_owner
        ):
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


@transaction.atomic
def settle_operation_attempt(
    operation: ProtocolOperation,
    *,
    attempt_token: uuid.UUID,
    response_payload: dict[str, object] | None = None,
    error_code: str = "",
    error_description: str = "",
) -> ProtocolOperation:
    """Settle only the currently owning delivery attempt.

    Late outcomes from superseded attempts are ignored and cannot overwrite a
    newer retry or a terminal result.
    """
    current = ProtocolOperation.objects.select_for_update().get(pk=operation.pk)
    if current.status != ProtocolOperation.Status.DELIVERING:
        return current
    if current.attempt_token != attempt_token:
        return current
    current.response_payload = response_payload
    current.error_code = error_code
    current.error_description = error_description
    current.status = (
        ProtocolOperation.Status.ERRORED
        if error_code
        else ProtocolOperation.Status.COMPLETED
    )
    current.completed_at = timezone.now()
    current.save(
        update_fields=(
            "response_payload",
            "error_code",
            "error_description",
            "status",
            "completed_at",
        )
    )
    return current
