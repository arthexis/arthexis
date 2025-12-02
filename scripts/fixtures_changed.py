"""Helpers for determining when fixtures need to be reloaded."""

from __future__ import annotations


def fixtures_changed(
    *,
    fixtures_present: bool,
    current_hash: str,
    stored_hash: str,
    migrations_changed: bool,
    clean: bool,
) -> bool:
    """Return ``True`` when fixtures should be reloaded.

    Reloads occur when fixtures exist and one of the following is true:
    * the ``--clean`` flag was provided,
    * migrations changed since the last refresh, or
    * the fixture hash differs from the stored value.
    """

    if not fixtures_present:
        return False

    return clean or migrations_changed or current_hash != stored_hash
