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
    resolution: str = ProtocolOperation.ReconciliationResolution.ACHIEVED,
) -> ProtocolOperation:
    now = timezone.now()
    operation.status = ProtocolOperation.Status.COMPLETED
    operation.completed_at = now
    operation.reconciliation_checked_at = now
    operation.reconciled_at = now
    operation.reconciliation_resolution = resolution
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



CONFIGURATION_RECONCILE_ACTIONS = frozenset({"ChangeConfiguration", "SetVariables"})


def _configuration_observation_request(
    operation: ProtocolOperation,
) -> tuple[str, dict[str, object]] | None:
    payload = operation.request_payload
    if operation.action == "ChangeConfiguration":
        key = payload.get("key")
        value = payload.get("value")
        if not isinstance(key, str) or not key or not isinstance(value, str):
            return None
        return "GetConfiguration", {"key": [key]}

    if operation.action != "SetVariables":
        return None
    requested = payload.get("setVariableData")
    if not isinstance(requested, list) or not requested:
        return None

    observations: list[dict[str, object]] = []
    for item in requested:
        if not isinstance(item, dict):
            return None
        component = item.get("component")
        variable = item.get("variable")
        value = item.get("attributeValue")
        if (
            not isinstance(component, dict)
            or not isinstance(component.get("name"), str)
            or not isinstance(variable, dict)
            or not isinstance(variable.get("name"), str)
            or not isinstance(value, str)
        ):
            return None
        observation: dict[str, object] = {
            "component": component,
            "variable": variable,
        }
        attribute_type = item.get("attributeType")
        if isinstance(attribute_type, str) and attribute_type:
            observation["attributeType"] = attribute_type
        observations.append(observation)
    return "GetVariables", {"getVariableData": observations}


def _latest_configuration_observation(
    operation: ProtocolOperation,
    *,
    action: str,
    payload: dict[str, object],
) -> ProtocolOperation | None:
    attempt_at = operation.last_attempt_at or operation.first_attempt_at or operation.created_at
    candidates = (
        ProtocolOperation.objects.filter(
            charger=operation.charger,
            version=operation.version,
            direction=ProtocolOperation.Direction.CSMS_TO_CHARGE_POINT,
            action=action,
            created_at__gte=attempt_at,
        )
        .exclude(pk=operation.pk)
        .order_by("-created_at", "-pk")
    )
    return next(
        (
            candidate
            for candidate in candidates[:20]
            if candidate.request_payload == payload
        ),
        None,
    )


def _v16_configuration_evidence(
    operation: ProtocolOperation,
    observation: ProtocolOperation,
) -> bool | None:
    key = operation.request_payload.get("key")
    desired = operation.request_payload.get("value")
    response = observation.response_payload
    if not isinstance(key, str) or not isinstance(desired, str) or not isinstance(response, dict):
        return None
    values = response.get("configurationKey")
    if not isinstance(values, list):
        return None
    for item in values:
        if not isinstance(item, dict) or item.get("key") != key:
            continue
        observed = item.get("value")
        if not isinstance(observed, str):
            return None
        return observed == desired
    return None


def _variable_identity(item: dict[str, object]) -> tuple[str, str, str] | None:
    component = item.get("component")
    variable = item.get("variable")
    if not isinstance(component, dict) or not isinstance(variable, dict):
        return None
    component_name = component.get("name")
    variable_name = variable.get("name")
    if not isinstance(component_name, str) or not isinstance(variable_name, str):
        return None
    attribute_type = item.get("attributeType", "Actual")
    if not isinstance(attribute_type, str):
        return None
    return component_name, variable_name, attribute_type


def _v201_variable_evidence(
    operation: ProtocolOperation,
    observation: ProtocolOperation,
) -> bool | None:
    requested = operation.request_payload.get("setVariableData")
    response = observation.response_payload
    if not isinstance(requested, list) or not isinstance(response, dict):
        return None
    results = response.get("getVariableResult")
    if not isinstance(results, list):
        return None

    observed_values: dict[tuple[str, str, str], str] = {}
    for item in results:
        if not isinstance(item, dict) or item.get("attributeStatus") != "Accepted":
            continue
        identity = _variable_identity(item)
        value = item.get("attributeValue")
        if identity is not None and isinstance(value, str):
            observed_values[identity] = value

    desired_values: dict[tuple[str, str, str], str] = {}
    for item in requested:
        if not isinstance(item, dict):
            return None
        identity = _variable_identity(item)
        value = item.get("attributeValue")
        if identity is None or not isinstance(value, str):
            return None
        desired_values[identity] = value

    if not desired_values or not desired_values.keys() <= observed_values.keys():
        return None
    return all(observed_values[key] == value for key, value in desired_values.items())


def _configuration_evidence(
    operation: ProtocolOperation,
    observation: ProtocolOperation,
) -> bool | None:
    if operation.action == "ChangeConfiguration":
        return _v16_configuration_evidence(operation, observation)
    if operation.action == "SetVariables":
        return _v201_variable_evidence(operation, observation)
    return None


@transaction.atomic
def reconcile_configuration_operation(operation: ProtocolOperation) -> ProtocolOperation:
    """Reconcile ambiguous configuration mutations through separate safe observations."""
    current = (
        ProtocolOperation.objects.select_for_update()
        .select_related("charger")
        .get(pk=operation.pk)
    )
    if current.status != ProtocolOperation.Status.RECOVERY_REQUIRED:
        return current
    if current.recovery_policy != ProtocolOperation.RecoveryPolicy.RECONCILE:
        return current
    if current.action not in CONFIGURATION_RECONCILE_ACTIONS:
        return current

    current.reconciliation_checked_at = timezone.now()
    current.save(update_fields=("reconciliation_checked_at",))

    observation_spec = _configuration_observation_request(current)
    if observation_spec is None:
        return current
    observation_action, observation_payload = observation_spec
    observation = _latest_configuration_observation(
        current,
        action=observation_action,
        payload=observation_payload,
    )
    if observation is None:
        create_operation(
            charger=current.charger,
            version=ProtocolVersion(current.version),
            direction=Direction.CSMS_TO_CHARGE_POINT,
            action=observation_action,
            request_payload=observation_payload,
        )
        return current
    if observation.status != ProtocolOperation.Status.COMPLETED:
        return current

    achieved = _configuration_evidence(current, observation)
    if achieved is None:
        return current
    if achieved:
        return _settle_reconciled_operation(
            current,
            basis=(
                f"{observation.action} operation {observation.pk} observed the requested "
                f"configuration state after ambiguous {current.action} intent."
            ),
        )

    retry = create_operation(
        charger=current.charger,
        version=ProtocolVersion(current.version),
        direction=Direction.CSMS_TO_CHARGE_POINT,
        action=current.action,
        request_payload=current.request_payload,
    )
    return _settle_reconciled_operation(
        current,
        resolution=ProtocolOperation.ReconciliationResolution.NOT_ACHIEVED,
        basis=(
            f"{observation.action} operation {observation.pk} proved the requested state "
            f"was not achieved; created deliberate replacement operation {retry.pk}."
        ),
    )


def pending_configuration_observation_ids(*, limit: int = 100) -> tuple[int, ...]:
    """Return matching pending observations for ambiguous configuration mutations."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    ids: list[int] = []
    operations = (
        ProtocolOperation.objects.filter(
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            recovery_policy=ProtocolOperation.RecoveryPolicy.RECONCILE,
            action__in=CONFIGURATION_RECONCILE_ACTIONS,
        )
        .select_related("charger")
        .order_by(F("reconciliation_checked_at").asc(nulls_first=True), "pk")[:limit]
    )
    for operation in operations:
        spec = _configuration_observation_request(operation)
        if spec is None:
            continue
        action, payload = spec
        observation = _latest_configuration_observation(
            operation,
            action=action,
            payload=payload,
        )
        if observation is not None and observation.status == ProtocolOperation.Status.PENDING:
            ids.append(observation.pk)
    return tuple(ids)


def reconcile_configuration_operations(*, limit: int = 100) -> int:
    """Reconcile one fair bounded batch of ambiguous configuration mutations."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    operation_ids = tuple(
        ProtocolOperation.objects.filter(
            status=ProtocolOperation.Status.RECOVERY_REQUIRED,
            recovery_policy=ProtocolOperation.RecoveryPolicy.RECONCILE,
            action__in=CONFIGURATION_RECONCILE_ACTIONS,
        )
        .order_by(F("reconciliation_checked_at").asc(nulls_first=True), "pk")
        .values_list("pk", flat=True)[:limit]
    )
    resolved = 0
    for operation_id in operation_ids:
        current = reconcile_configuration_operation(ProtocolOperation(pk=operation_id))
        if (
            current.status == ProtocolOperation.Status.COMPLETED
            and current.reconciled_at is not None
        ):
            resolved += 1
    return resolved


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
