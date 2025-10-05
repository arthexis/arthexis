"""Shared helpers for RFID import and export workflows."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping

from core.models import EnergyAccount, RFID


def account_column_for_field(account_field: str) -> str:
    """Return the column name that should be used for the account field.

    Args:
        account_field: Either ``"id"`` or ``"name"`` depending on how energy
            accounts should be represented.

    Returns:
        The CSV column header to use for the selected account field.
    """

    return "energy_account_names" if account_field == "name" else "energy_accounts"


def serialize_accounts(tag: RFID, account_field: str) -> str:
    """Convert the RFID's accounts to a serialized string."""

    accounts = tag.energy_accounts.all()
    if account_field == "name":
        return ",".join(account.name for account in accounts if account.name)
    return ",".join(str(account.id) for account in accounts)


def _normalized_unique_names(values: Iterable[str]) -> list[str]:
    """Return a list of unique, normalized account names preserving order."""

    seen: OrderedDict[str, None] = OrderedDict()
    for value in values:
        normalized = value.strip().upper()
        if not normalized:
            continue
        if normalized not in seen:
            seen[normalized] = None
    return list(seen.keys())


def _accounts_from_ids(values: Iterable[str]) -> list[EnergyAccount]:
    """Resolve a list of account ids into EnergyAccount instances."""

    identifiers: list[int] = []
    for value in values:
        value = value.strip()
        if not value:
            continue
        try:
            identifiers.append(int(value))
        except (TypeError, ValueError):
            continue
    if not identifiers:
        return []
    existing = EnergyAccount.objects.in_bulk(identifiers)
    return [existing[idx] for idx in identifiers if idx in existing]


def parse_accounts(row: Mapping[str, object], account_field: str) -> list[EnergyAccount]:
    """Resolve energy accounts for an RFID import row.

    Args:
        row: Mapping of column names to raw values for the import row.
        account_field: Preferred field (``"id"`` or ``"name"``) describing how
            accounts are encoded.

    Returns:
        A list of :class:`EnergyAccount` instances. The list will be empty when
        no accounts should be linked.
    """

    preferred_column = account_column_for_field(account_field)
    fallback_column = (
        "energy_accounts"
        if preferred_column == "energy_account_names"
        else "energy_account_names"
    )

    def _value_for(column: str) -> str:
        raw = row.get(column, "")
        if raw is None:
            return ""
        return str(raw).strip()

    raw_value = _value_for(preferred_column)
    effective_field = account_field

    if not raw_value:
        raw_value = _value_for(fallback_column)
        if raw_value:
            effective_field = (
                "name" if fallback_column == "energy_account_names" else "id"
            )

    if not raw_value:
        return []

    parts = raw_value.split(",")
    if effective_field == "name":
        accounts: list[EnergyAccount] = []
        for normalized_name in _normalized_unique_names(parts):
            account, _ = EnergyAccount.objects.get_or_create(name=normalized_name)
            accounts.append(account)
        return accounts

    return _accounts_from_ids(parts)

