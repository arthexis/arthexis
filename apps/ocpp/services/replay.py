"""Durable persistence services for inbound OCPP replay."""

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.ocpp.models import Charger, InboundProtocolRequest
from apps.ocpp.protocol.contracts import Direction, ProtocolVersion
from apps.ocpp.protocol.frames import CallError, CallResult
from apps.ocpp.protocol.replay import (
    ReplayIdentity,
    ReplayPolicy,
    identity_key,
    replay_identity,
)


@dataclass(frozen=True)
class ReplayAcquisition:
    """Result of acquiring one durable logical inbound request."""

    request: InboundProtocolRequest
    created: bool

    @property
    def completed(self) -> bool:
        return self.request.status == InboundProtocolRequest.Status.COMPLETED

    @property
    def stale(self) -> bool:
        return self.request.status == InboundProtocolRequest.Status.STALE


def acquire_inbound_request(
    *,
    charger: Charger,
    version: ProtocolVersion,
    action: str,
    call_id: str,
    payload: dict[str, object],
    policy: ReplayPolicy = ReplayPolicy.CALL_ID_AND_FINGERPRINT,
    domain_identity: str = "",
) -> ReplayAcquisition:
    """Create or recover the durable record for one logical inbound request."""
    identity = replay_identity(
        version=version,
        action=action,
        call_id=call_id,
        payload=payload,
        policy=policy,
        domain_identity=domain_identity,
    )
    key = identity_key(identity)
    if policy is ReplayPolicy.NO_CROSS_CALL_DEDUP:
        return _acquire_bounded_request(
            charger=charger,
            version=version,
            action=action,
            payload=payload,
            identity=identity,
            key=key,
        )

    request, created = InboundProtocolRequest.objects.get_or_create(
        charger=charger,
        version=version.value,
        direction=Direction.CHARGE_POINT_TO_CSMS.value,
        action=action,
        identity_key=key,
        replay_generation="",
        defaults={
            "unique_id": call_id,
            "fingerprint": identity.fingerprint,
            "replay_policy": policy.value,
            "domain_identity": domain_identity,
            "request_payload": payload,
        },
    )
    if not created:
        request = _mark_stale_if_needed(request)
    return ReplayAcquisition(request=request, created=created)


def complete_with_result(
    request: InboundProtocolRequest,
    *,
    payload: dict[str, object],
) -> InboundProtocolRequest:
    """Persist one successful response, accepting identical repeat completion."""
    return _complete(
        request,
        response_kind=InboundProtocolRequest.ResponseKind.RESULT,
        response_payload=payload,
    )


def complete_with_error(
    request: InboundProtocolRequest,
    *,
    code: str,
    description: str,
    details: dict[str, object],
) -> InboundProtocolRequest:
    """Persist one OCPP CallError, accepting identical repeat completion."""
    return _complete(
        request,
        response_kind=InboundProtocolRequest.ResponseKind.ERROR,
        error_code=code,
        error_description=description,
        error_details=details,
    )


def stored_response(
    request: InboundProtocolRequest,
    *,
    call_id: str | None = None,
) -> CallResult | CallError:
    """Reconstruct a completed response using the current inbound CALL ID."""
    if request.status != InboundProtocolRequest.Status.COMPLETED:
        raise ValueError("Inbound request has not completed.")

    response_call_id = call_id or request.unique_id
    if request.response_kind == InboundProtocolRequest.ResponseKind.RESULT:
        if not isinstance(request.response_payload, dict):
            raise ValueError("Completed CallResult is missing its payload.")
        return CallResult(unique_id=response_call_id, payload=request.response_payload)

    if request.response_kind == InboundProtocolRequest.ResponseKind.ERROR:
        if not isinstance(request.error_details, dict):
            raise ValueError("Completed CallError is missing its details.")
        return CallError(
            unique_id=response_call_id,
            code=request.error_code,
            description=request.error_description,
            details=request.error_details,
        )

    raise ValueError("Completed inbound request has no response kind.")


@transaction.atomic
def _complete(
    request: InboundProtocolRequest,
    *,
    response_kind: str,
    response_payload: dict[str, object] | None = None,
    error_code: str = "",
    error_description: str = "",
    error_details: dict[str, object] | None = None,
) -> InboundProtocolRequest:
    current = InboundProtocolRequest.objects.select_for_update().get(pk=request.pk)
    expected = _response_signature(
        response_kind=response_kind,
        response_payload=response_payload,
        error_code=error_code,
        error_description=error_description,
        error_details=error_details,
    )

    if current.status == InboundProtocolRequest.Status.COMPLETED:
        actual = _response_signature(
            response_kind=current.response_kind,
            response_payload=current.response_payload,
            error_code=current.error_code,
            error_description=current.error_description,
            error_details=current.error_details,
        )
        if actual != expected:
            raise ValueError("Inbound request already completed with another response.")
        return current

    current.status = InboundProtocolRequest.Status.COMPLETED
    current.response_kind = response_kind
    current.response_payload = response_payload
    current.error_code = error_code
    current.error_description = error_description
    current.error_details = error_details
    current.completed_at = timezone.now()
    current.save(
        update_fields=(
            "status",
            "response_kind",
            "response_payload",
            "error_code",
            "error_description",
            "error_details",
            "completed_at",
        )
    )
    return current


def _response_signature(
    *,
    response_kind: str,
    response_payload: object,
    error_code: str,
    error_description: str,
    error_details: object,
) -> tuple[object, ...]:
    return (
        response_kind,
        response_payload,
        error_code,
        error_description,
        error_details,
    )


@transaction.atomic
def _mark_stale_if_needed(
    request: InboundProtocolRequest,
) -> InboundProtocolRequest:
    current = InboundProtocolRequest.objects.select_for_update().get(pk=request.pk)
    if current.status != InboundProtocolRequest.Status.PROCESSING:
        return current
    cutoff = timezone.now() - timedelta(seconds=settings.OCPP_REPLAY_STALE_SECONDS)
    if current.received_at > cutoff:
        return current
    current.status = InboundProtocolRequest.Status.STALE
    current.stale_at = timezone.now()
    current.save(update_fields=("status", "stale_at"))
    return current



@transaction.atomic
def _acquire_bounded_request(
    *,
    charger: Charger,
    version: ProtocolVersion,
    action: str,
    payload: dict[str, object],
    identity: ReplayIdentity,
    key: str,
) -> ReplayAcquisition:
    if settings.OCPP_REPLAY_WINDOW_SECONDS <= 0:
        raise ValueError("OCPP_REPLAY_WINDOW_SECONDS must be positive.")

    lookup = {
        "charger": charger,
        "version": version.value,
        "direction": Direction.CHARGE_POINT_TO_CSMS.value,
        "action": action,
        "identity_key": key,
        "replay_generation": "active",
    }
    active = (
        InboundProtocolRequest.objects.select_for_update()
        .filter(**lookup)
        .first()
    )
    if active is not None:
        cutoff = timezone.now() - timedelta(
            seconds=settings.OCPP_REPLAY_WINDOW_SECONDS
        )
        if active.received_at >= cutoff:
            return ReplayAcquisition(
                request=_mark_stale_if_needed(active),
                created=False,
            )
        active.replay_generation = f"archived:{active.pk}"
        active.save(update_fields=("replay_generation",))

    request, created = InboundProtocolRequest.objects.get_or_create(
        **lookup,
        defaults={
            "unique_id": identity.call_id,
            "fingerprint": identity.fingerprint,
            "replay_policy": ReplayPolicy.NO_CROSS_CALL_DEDUP.value,
            "request_payload": payload,
        },
    )
    if not created:
        request = _mark_stale_if_needed(request)
    return ReplayAcquisition(request=request, created=created)



@transaction.atomic
def reopen_stale_request(
    request: InboundProtocolRequest,
) -> InboundProtocolRequest:
    """Return one known-idempotent stale request to PROCESSING for safe retry."""
    current = InboundProtocolRequest.objects.select_for_update().get(pk=request.pk)
    if current.status != InboundProtocolRequest.Status.STALE:
        return current
    current.status = InboundProtocolRequest.Status.PROCESSING
    current.stale_at = None
    current.save(update_fields=("status", "stale_at"))
    return current
