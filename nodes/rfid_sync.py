"""Helper utilities for synchronizing RFID records between nodes."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from django.utils.dateparse import parse_datetime

from core.models import CustomerAccount, RFID

if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from nodes.models import Node


@dataclass(slots=True)
class RFIDSyncOutcome:
    """Result of applying an RFID payload to the local database."""

    instance: RFID | None = None
    created: bool = False
    updated: bool = False
    accounts_linked: int = 0
    missing_accounts: list[str] = field(default_factory=list)
    account_data_provided: bool = False
    ok: bool = False
    error: str | None = None


def serialize_rfid(tag: RFID) -> dict[str, Any]:
    """Return a dictionary representation suitable for the node API."""

    accounts = list(tag.energy_accounts.all())
    id_values = [account.id for account in accounts]
    name_values = [account.name for account in accounts if account.name]
    payload = {
        "rfid": tag.rfid,
        "custom_label": tag.custom_label,
        "key_a": tag.key_a,
        "key_b": tag.key_b,
        "data": tag.data,
        "key_a_verified": tag.key_a_verified,
        "key_b_verified": tag.key_b_verified,
        "allowed": tag.allowed,
        "color": tag.color,
        "kind": tag.kind,
        "released": tag.released,
        "external_command": tag.external_command,
        "post_auth_command": tag.post_auth_command,
        "last_seen_on": tag.last_seen_on.isoformat() if tag.last_seen_on else None,
        "customer_accounts": id_values,
        "customer_account_names": name_values,
    }
    payload["energy_accounts"] = id_values
    payload["energy_account_names"] = name_values
    return payload


def apply_rfid_payload(
    entry: Mapping[str, Any], *, origin_node: Node | None = None
) -> RFIDSyncOutcome:
    """Create or update an :class:`RFID` instance from API payload data."""

    outcome = RFIDSyncOutcome()
    rfid_value = str(entry.get("rfid") or "").strip()
    if not rfid_value:
        outcome.error = "Missing RFID value"
        return outcome

    external_command = entry.get("external_command")
    if not isinstance(external_command, str):
        external_command = ""
    else:
        external_command = external_command.strip()
    post_auth_command = entry.get("post_auth_command")
    if not isinstance(post_auth_command, str):
        post_auth_command = ""
    else:
        post_auth_command = post_auth_command.strip()

    defaults: dict[str, Any] = {
        "custom_label": entry.get("custom_label", ""),
        "key_a": entry.get("key_a", RFID._meta.get_field("key_a").default),
        "key_b": entry.get("key_b", RFID._meta.get_field("key_b").default),
        "data": entry.get("data", []) or [],
        "key_a_verified": bool(entry.get("key_a_verified", False)),
        "key_b_verified": bool(entry.get("key_b_verified", False)),
        "allowed": bool(entry.get("allowed", True)),
        "color": entry.get("color", RFID.BLACK),
        "kind": entry.get("kind", RFID.CLASSIC),
        "released": bool(entry.get("released", False)),
        "external_command": external_command,
        "post_auth_command": post_auth_command,
    }

    if origin_node is not None:
        defaults["origin_node"] = origin_node

    if "last_seen_on" in entry:
        last_seen = entry.get("last_seen_on")
        defaults["last_seen_on"] = parse_datetime(last_seen) if last_seen else None

    obj, created = RFID.update_or_create_from_code(rfid_value, defaults=defaults)

    outcome.instance = obj
    outcome.created = created
    outcome.updated = not created
    outcome.ok = True

    accounts, missing, provided = _resolve_accounts(entry)
    outcome.account_data_provided = provided
    if provided:
        obj.energy_accounts.set(accounts)
        outcome.accounts_linked = len(accounts)
    else:
        outcome.accounts_linked = 0
    outcome.missing_accounts = missing

    return outcome


def _resolve_accounts(
    entry: Mapping[str, Any]
) -> tuple[list[CustomerAccount], list[str], bool]:
    """Return matching accounts and missing identifiers from payload data."""

    has_account_data = any(
        key in entry
        for key in ("customer_accounts", "energy_accounts", "customer_account_names", "energy_account_names")
    )
    if not has_account_data:
        return [], [], False

    accounts: list[CustomerAccount] = []
    missing: list[str] = []
    seen_ids: set[int] = set()
    matched_names: "OrderedDict[str, None]" = OrderedDict()

    # Resolve by numeric identifiers first to preserve ordering.
    id_values = _coerce_values(
        entry.get("customer_accounts") or entry.get("energy_accounts")
    )
    parsed_ids: list[tuple[str, int]] = []
    invalid_ids: list[str] = []
    for raw in id_values:
        try:
            parsed_ids.append((raw, int(raw)))
        except (TypeError, ValueError):
            invalid_ids.append(raw)

    existing_by_id = (
        CustomerAccount.objects.in_bulk([pk for _, pk in parsed_ids])
        if parsed_ids
        else {}
    )

    for raw, pk in parsed_ids:
        account = existing_by_id.get(pk)
        if account and account.id not in seen_ids:
            accounts.append(account)
            seen_ids.add(account.id)
            if account.name:
                matched_names[account.name.strip().upper()] = None
        else:
            missing.append(raw)

    missing.extend(invalid_ids)

    # Resolve remaining accounts by name.
    name_values = _coerce_values(
        entry.get("customer_account_names") or entry.get("energy_account_names")
    )
    processed_names: "OrderedDict[str, None]" = OrderedDict()
    for raw in name_values:
        normalized = raw.strip().upper()
        if not normalized or normalized in processed_names:
            continue
        processed_names[normalized] = None
        if normalized in matched_names:
            continue
        account = (
            CustomerAccount.objects.filter(name__iexact=raw.strip())
            .order_by("pk")
            .first()
        )
        if account and account.id not in seen_ids:
            accounts.append(account)
            seen_ids.add(account.id)
            if account.name:
                matched_names[account.name.strip().upper()] = None
        else:
            missing.append(raw)

    # Deduplicate missing entries while preserving order.
    missing_unique = list(OrderedDict.fromkeys(raw for raw in missing if raw))

    return accounts, missing_unique, True


def _coerce_values(values: Any) -> list[str]:
    """Return a list of trimmed string values from the payload field."""

    if values is None:
        return []
    if isinstance(values, str):
        values = values.split(",")
    if isinstance(values, Mapping):
        values = list(values.values())
    if not isinstance(values, Iterable) or isinstance(values, (bytes, bytearray)):
        return []

    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            result.append(text)
    return result

