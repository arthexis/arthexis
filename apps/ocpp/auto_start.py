"""Persistent automatic remote-start reservation transitions."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.ocpp.models import AutoStartAttempt, Charger

REQUEST_TIMEOUT = timedelta(seconds=90)
RETRY_DELAY = timedelta(seconds=15)
ACTIVE_STATES = (
    AutoStartAttempt.State.REQUESTED,
    AutoStartAttempt.State.ACCEPTED,
    AutoStartAttempt.State.STARTED,
)


def _station_charger(*, charger_pk: int, lock: bool = False) -> Charger | None:
    """Return one stable row used to coordinate a station's attempts."""

    charger_id = (
        Charger.objects.filter(pk=charger_pk)
        .values_list("charger_id", flat=True)
        .first()
    )
    if not charger_id:
        return None
    chargers = Charger.objects.filter(charger_id=charger_id).order_by("pk")
    if lock:
        chargers = chargers.select_for_update()
    return chargers.first()


def reserve_attempt(
    *,
    charger_pk: int,
    reservation_scope: str,
    id_tag: str,
    message_id: str,
    action: str,
) -> AutoStartAttempt | None:
    """Persist one active attempt unless an active or cooling-down attempt exists."""

    now = timezone.now()
    with transaction.atomic():
        station_charger = _station_charger(charger_pk=charger_pk, lock=True)
        if station_charger is None:
            return None
        attempts = AutoStartAttempt.objects.select_for_update().filter(
            charger__charger_id=station_charger.charger_id,
            reservation_scope=reservation_scope,
        )
        expired_attempt_ids = list(
            attempts.filter(
                state__in=(
                    AutoStartAttempt.State.REQUESTED,
                    AutoStartAttempt.State.ACCEPTED,
                ),
                expires_at__lte=now,
            ).values_list("pk", flat=True)
        )
        attempts.filter(pk__in=expired_attempt_ids).update(
            state=AutoStartAttempt.State.TIMED_OUT,
            retry_after=now + RETRY_DELAY,
            completed_at=now,
        )
        if attempts.filter(state__in=ACTIVE_STATES).exists():
            return None
        # An expired request was timed out by this notification, so retry it now:
        # a charger may not emit another status notification after the cooldown.
        # Keep the cooldown for rejections and call errors from earlier requests.
        if (
            attempts.exclude(pk__in=expired_attempt_ids)
            .filter(retry_after__gt=now)
            .exists()
        ):
            return None
        try:
            return AutoStartAttempt.objects.create(
                charger=station_charger,
                reservation_scope=reservation_scope,
                id_tag=id_tag,
                message_id=message_id,
                action=action,
                expires_at=now + REQUEST_TIMEOUT,
            )
        except IntegrityError:
            # A concurrent status notification won the partial unique constraint.
            return None


def transition_attempt(
    attempt_id: UUID | str,
    *,
    state: AutoStartAttempt.State,
    response_payload: dict | None = None,
    retry: bool = False,
    from_states: tuple[str, ...] = ACTIVE_STATES,
) -> bool:
    """Move exactly one active attempt to its next state."""

    now = timezone.now()
    updates: dict[str, object] = {
        "state": state,
        "completed_at": now if state not in ACTIVE_STATES else None,
    }
    if response_payload is not None:
        updates["response_payload"] = response_payload
    if state == AutoStartAttempt.State.ACCEPTED:
        updates["expires_at"] = now + REQUEST_TIMEOUT
    if retry:
        updates["retry_after"] = now + RETRY_DELAY
    return bool(
        AutoStartAttempt.objects.filter(
            attempt_id=attempt_id,
            state__in=from_states,
        ).update(**updates)
    )


def release_scope(*, charger_pk: int, reservation_scope: str) -> int:
    """Release station-scoped attempts once its EVSE leaves plug-in state."""

    station_charger = _station_charger(charger_pk=charger_pk)
    if station_charger is None:
        return 0
    now = timezone.now()
    return AutoStartAttempt.objects.filter(
        charger__charger_id=station_charger.charger_id,
        reservation_scope=reservation_scope,
        state__in=ACTIVE_STATES,
    ).update(state=AutoStartAttempt.State.RELEASED, completed_at=now)


def release_chargers(*, charger_ids: list[int]) -> int:
    """Release active attempts when auto-start configuration is changed."""

    if not charger_ids:
        return 0
    station_ids = Charger.objects.filter(pk__in=charger_ids).values_list(
        "charger_id", flat=True
    )
    now = timezone.now()
    return AutoStartAttempt.objects.filter(
        charger__charger_id__in=station_ids,
        state__in=ACTIVE_STATES,
    ).update(state=AutoStartAttempt.State.RELEASED, completed_at=now)


def mark_scope_started(*, charger_pk: int, reservation_scope: str) -> bool:
    """Keep station-scoped matching requests reserved after charging begins."""

    charger_id = (
        Charger.objects.filter(pk=charger_pk)
        .values_list("charger_id", flat=True)
        .first()
    )
    if not charger_id:
        return False

    return bool(
        AutoStartAttempt.objects.filter(
            charger__charger_id=charger_id,
            reservation_scope=reservation_scope,
            state__in=(
                AutoStartAttempt.State.REQUESTED,
                AutoStartAttempt.State.ACCEPTED,
            ),
        ).update(
            state=AutoStartAttempt.State.STARTED,
            expires_at=None,
            completed_at=None,
        )
    )


def apply_call_result(*, metadata: dict, payload: dict) -> bool:
    """Record an auto-start result without affecting a later attempt."""

    attempt_id = metadata.get("auto_start_attempt_id")
    if not attempt_id:
        return False
    accepted = str(payload.get("status") or "").strip().casefold() == "accepted"
    return transition_attempt(
        str(attempt_id),
        state=(
            AutoStartAttempt.State.ACCEPTED
            if accepted
            else AutoStartAttempt.State.REJECTED
        ),
        response_payload=payload,
        retry=not accepted,
        from_states=(AutoStartAttempt.State.REQUESTED,),
    )


def apply_call_error(
    *, metadata: dict, error_code: str | None, description: str | None
) -> bool:
    """Record a call error for exactly the associated auto-start attempt."""

    attempt_id = metadata.get("auto_start_attempt_id")
    if not attempt_id:
        return False
    return transition_attempt(
        str(attempt_id),
        state=AutoStartAttempt.State.FAILED,
        response_payload={
            "errorCode": error_code or "",
            "errorDescription": description or "",
        },
        retry=True,
        from_states=(AutoStartAttempt.State.REQUESTED,),
    )
