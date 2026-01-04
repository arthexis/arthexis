"""Helpers for determining when fixtures need to be reloaded."""

from __future__ import annotations


def fixtures_changed(
    *,
    fixtures_present: bool,
    current_hash: str,
    stored_hash: str,
    migrations_changed: bool,
    migrations_ran: bool,
    current_by_app: dict[str, str] | None = None,
    stored_by_app: dict[str, str] | None = None,
    clean: bool,
) -> bool:
    """Return ``True`` when fixtures should be reloaded.

    Reloads occur when fixtures exist and one of the following is true:
    * the ``--clean`` flag was provided,
    * migrations changed or ran since the last refresh, or
    * the fixture hash differs from the stored value.
    """

    if not fixtures_present:
        return False

    if clean or migrations_changed or migrations_ran:
        return True

    if current_by_app is not None and stored_by_app is not None:
        if current_by_app != stored_by_app:
            return True

    return current_hash != stored_hash
