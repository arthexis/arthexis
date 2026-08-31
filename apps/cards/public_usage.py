from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.db.models import Q
from django.utils import timezone

from apps.cards.models import RFID, RFIDAttempt

OCPP_ID_TAG_LENGTH = 20


def _prefix_claimed_by_other_card(
    prefix: str,
    tag: RFID,
    *,
    include_reversed_uid: bool = True,
) -> bool:
    normalized = RFID.normalize_code(prefix)
    if not normalized:
        return False
    query = Q(rfid__istartswith=normalized)
    if include_reversed_uid:
        query |= Q(reversed_uid__istartswith=normalized)
    return RFID.objects.exclude(pk=tag.pk).filter(query).exists()


def _value_has_other_card_prefix(
    value: str,
    tag: RFID,
    *,
    include_reversed_uid: bool = True,
) -> bool:
    prefix_length = min(len(value), RFID.MATCH_PREFIX_LENGTH)
    if not prefix_length:
        return False
    match_prefix = value[:prefix_length]
    query = Q(rfid__istartswith=match_prefix)
    if include_reversed_uid:
        query |= Q(reversed_uid__istartswith=match_prefix)
    matches = RFID.objects.exclude(pk=tag.pk).filter(query)
    for rfid, reversed_uid in matches.values_list("rfid", "reversed_uid"):
        owned_values = (rfid, reversed_uid) if include_reversed_uid else (rfid,)
        for owned_value in owned_values:
            normalized = RFID.normalize_code(owned_value)
            if normalized and normalized != value and value.startswith(normalized):
                return True
    return False


def _value_shares_resolver_prefix_with_other_card(value: str, tag: RFID) -> bool:
    normalized = RFID.normalize_code(value)
    if not normalized:
        return False
    candidates = [normalized]
    reversed_uid = RFID.reverse_uid(normalized)
    if reversed_uid and reversed_uid != normalized:
        candidates.append(reversed_uid)

    query = Q()
    has_prefix = False
    for candidate in candidates:
        prefix_length = min(len(candidate), RFID.MATCH_PREFIX_LENGTH)
        if not prefix_length:
            continue
        resolver_prefix = candidate[:prefix_length]
        query |= Q(rfid__istartswith=resolver_prefix)
        has_prefix = True
    if not has_prefix:
        return False
    return RFID.objects.exclude(pk=tag.pk).filter(query).exists()


def _value_claimed_by_other_card(value: str, tag: RFID) -> bool:
    normalized = RFID.normalize_code(value)
    if not normalized:
        return False
    directly_claimed = (
        RFID.objects.exclude(pk=tag.pk)
        .filter(Q(rfid__istartswith=normalized) | Q(reversed_uid__iexact=normalized))
        .exists()
    )
    return (
        directly_claimed
        or _value_has_other_card_prefix(
            normalized,
            tag,
            include_reversed_uid=False,
        )
        or _value_shares_resolver_prefix_with_other_card(normalized, tag)
    )


def _rfid_value_query(value: str, *, tag: RFID, field_name: str = "rfid") -> Q:
    normalized = RFID.normalize_code(value)
    if not normalized:
        return Q(pk__in=[])
    candidates = [normalized]
    reversed_uid = RFID.reverse_uid(normalized)
    if reversed_uid and reversed_uid != normalized:
        candidates.append(reversed_uid)
    query = Q()
    has_candidate = False
    for candidate in candidates:
        if _value_claimed_by_other_card(candidate, tag):
            continue
        query |= Q(**{f"{field_name}__iexact": candidate})
        if len(candidate) <= OCPP_ID_TAG_LENGTH:
            query |= Q(**{f"{field_name}__istartswith": candidate})
            if len(candidate) > RFID.MATCH_PREFIX_LENGTH and candidate == normalized:
                resolver_prefix = candidate[: RFID.MATCH_PREFIX_LENGTH]
                if (
                    not _prefix_claimed_by_other_card(resolver_prefix, tag)
                    and not _value_has_other_card_prefix(resolver_prefix, tag)
                    and not _value_shares_resolver_prefix_with_other_card(
                        resolver_prefix,
                        tag,
                    )
                ):
                    query |= Q(**{f"{field_name}__istartswith": resolver_prefix})
        has_candidate = True
    prefixes = {
        (candidate[:prefix_length], prefix_length)
        for candidate in candidates
        for prefix_length in (OCPP_ID_TAG_LENGTH, RFID.MATCH_PREFIX_LENGTH)
        if len(candidate) > prefix_length
    }
    for prefix, prefix_length in prefixes:
        if (
            _prefix_claimed_by_other_card(
                prefix,
                tag,
                include_reversed_uid=prefix_length == RFID.MATCH_PREFIX_LENGTH,
            )
            or _value_has_other_card_prefix(
                prefix,
                tag,
                include_reversed_uid=prefix_length == RFID.MATCH_PREFIX_LENGTH,
            )
            or _value_shares_resolver_prefix_with_other_card(
                prefix,
                tag,
            )
        ):
            continue
        query |= Q(**{f"{field_name}__iexact": prefix})
        has_candidate = True
    if not has_candidate:
        return Q(pk__in=[])
    return query


def _attempt_queryset(tag: RFID):
    query = Q(label_id=tag.pk)
    if tag.rfid:
        query |= _rfid_value_query(tag.rfid, tag=tag)
    return RFIDAttempt.objects.filter(query)


def _transaction_queryset(tag: RFID):
    try:
        Transaction = apps.get_model("ocpp", "Transaction")
    except LookupError:
        return None
    if not tag.rfid:
        return Transaction.objects.none()
    return (
        Transaction.objects.filter(_rfid_value_query(tag.rfid, tag=tag))
        .select_related("charger")
        .order_by("-start_time", "-pk")
    )


def _month_start(now) -> datetime:
    current = timezone.localtime(now)
    return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _transaction_energy(transaction) -> Decimal:
    value = Decimal(str(getattr(transaction, "kw", 0) or 0))
    return max(value, Decimal("0"))


def _public_charger_label(transaction) -> str:
    charger = getattr(transaction, "charger", None)
    if charger is None:
        return "Charge point"
    display_name = str(getattr(charger, "display_name", "") or "").strip()
    if display_name:
        if transaction.connector_id:
            return f"{display_name} connector {transaction.connector_id}"
        return display_name
    if transaction.connector_id:
        return f"Charge point connector {transaction.connector_id}"
    return "Charge point"


def _public_display_label(tag: RFID) -> str:
    return (
        (tag.command_card_name or "").strip()
        or (tag.custom_label or "").strip()
        or (tag.generated_label or "").strip()
        or "RFID card"
    )


def _public_transaction(transaction) -> dict[str, Any]:
    return {
        "started_at": transaction.start_time,
        "stopped_at": transaction.stop_time,
        "charger_label": _public_charger_label(transaction),
        "energy_kwh": _transaction_energy(transaction),
        "status": (
            "rejected"
            if transaction.authorization_status
            == transaction.AuthorizationStatus.REJECTED
            else "accepted"
        ),
    }


def _attempt_reason(attempt: RFIDAttempt) -> str:
    payload = attempt.payload if isinstance(attempt.payload, dict) else {}
    reason = (
        payload.get("authorization_reason")
        or payload.get("reason_code")
        or payload.get("reason")
        or ""
    )
    return str(reason).replace("_", " ").strip()[:80]


def _public_attempt(attempt: RFIDAttempt) -> dict[str, Any]:
    return {
        "attempted_at": attempt.attempted_at,
        "status": attempt.status,
        "source": attempt.source,
        "reason": _attempt_reason(attempt),
    }


def build_public_rfid_usage(tag: RFID) -> dict[str, Any]:
    """Return cardholder-safe public usage context for an RFID card."""

    attempts = _attempt_queryset(tag)
    transactions = _transaction_queryset(tag)
    now = timezone.now()
    month_start = _month_start(now)

    accepted_attempts = attempts.filter(status=RFIDAttempt.Status.ACCEPTED).count()
    rejected_attempts = attempts.filter(status=RFIDAttempt.Status.REJECTED).count()
    scanned_attempts = attempts.filter(status=RFIDAttempt.Status.SCANNED).count()
    latest_attempt = attempts.order_by("-attempted_at", "-pk").first()

    if transactions is not None:
        accepted_transactions = list(
            transactions.exclude(
                authorization_status=transactions.model.AuthorizationStatus.REJECTED
            )
        )
    else:
        accepted_transactions = []
    current_month_transactions = [
        transaction
        for transaction in accepted_transactions
        if transaction.start_time >= month_start
    ]

    total_energy = sum(
        (_transaction_energy(transaction) for transaction in accepted_transactions),
        Decimal("0"),
    )
    month_energy = sum(
        (
            _transaction_energy(transaction)
            for transaction in current_month_transactions
        ),
        Decimal("0"),
    )
    latest_transaction = accepted_transactions[0] if accepted_transactions else None
    last_used_at = None
    if latest_transaction is not None:
        last_used_at = latest_transaction.start_time
    if latest_attempt is not None and (
        last_used_at is None or latest_attempt.attempted_at > last_used_at
    ):
        last_used_at = latest_attempt.attempted_at

    return {
        "tag": tag,
        "display_label": _public_display_label(tag),
        "public_enabled": tag.public_token_enabled,
        "last_used_at": last_used_at,
        "total_sessions": len(accepted_transactions),
        "total_kwh": total_energy,
        "current_month_sessions": len(current_month_transactions),
        "current_month_kwh": month_energy,
        "accepted_scan_count": accepted_attempts,
        "rejected_scan_count": rejected_attempts,
        "scanned_count": scanned_attempts,
        "recent_transactions": [
            _public_transaction(transaction)
            for transaction in accepted_transactions[:10]
        ],
        "recent_attempts": [
            _public_attempt(attempt)
            for attempt in attempts.order_by("-attempted_at", "-pk")[:10]
        ],
    }
