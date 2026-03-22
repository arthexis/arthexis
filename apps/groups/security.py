"""Helpers for canonical staff security-group defaults."""

from __future__ import annotations

from collections.abc import Iterable

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from .constants import (
    EXTERNAL_AGENT_GROUP_NAME,
    NETWORK_OPERATOR_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
    SITE_OPERATOR_GROUP_NAME,
    STAFF_SECURITY_GROUP_NAMES,
)


ADMIN_DEFAULT_GROUP_NAMES: tuple[str, ...] = (SITE_OPERATOR_GROUP_NAME,)
SYSTEM_DEFAULT_GROUP_NAMES: tuple[str, ...] = (
    NETWORK_OPERATOR_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
)
DEFAULT_STAFF_GROUP_NAMES: tuple[str, ...] = (EXTERNAL_AGENT_GROUP_NAME,)


def ensure_security_groups_exist(group_names: Iterable[str]) -> dict[str, Group]:
    """Return existing groups for ``group_names``, creating any missing entries.

    Parameters:
        group_names: Canonical security-group names that must be present.

    Returns:
        Mapping of group name to the created or existing ``Group`` instance.
    """

    ordered_names = tuple(dict.fromkeys(name.strip() for name in group_names if name and name.strip()))
    groups: dict[str, Group] = {}
    for name in ordered_names:
        group, _ = Group.objects.get_or_create(name=name)
        groups[name] = group
    return groups


def get_default_staff_group_names(user, *, explicit_group_names: Iterable[str] | None = None) -> tuple[str, ...]:
    """Return canonical staff security groups that should be ensured for ``user``.

    Parameters:
        user: User-like object whose username and staff flags drive the defaults.
        explicit_group_names: Optional explicit group names provided by a caller.

    Returns:
        Tuple of canonical security-group names to ensure on the user.
    """

    username = getattr(user, "username", "") or ""
    explicit_names = {
        name.strip() for name in (explicit_group_names or ()) if name and name.strip()
    }
    user_model = get_user_model()

    if username == getattr(user_model, "ADMIN_USERNAME", "admin"):
        return ADMIN_DEFAULT_GROUP_NAMES
    if username == getattr(user_model, "SYSTEM_USERNAME", "arthexis"):
        return SYSTEM_DEFAULT_GROUP_NAMES
    if not getattr(user, "is_staff", False):
        return ()
    if explicit_names:
        return ()

    existing_staff_group_names = set(
        getattr(user, "groups", Group.objects.none())
        .filter(name__in=STAFF_SECURITY_GROUP_NAMES)
        .values_list("name", flat=True)
    )
    if existing_staff_group_names:
        return ()
    return DEFAULT_STAFF_GROUP_NAMES


def ensure_default_staff_groups(user, *, explicit_group_names: Iterable[str] | None = None) -> tuple[str, ...]:
    """Ensure canonical staff security-group defaults are present for ``user``.

    Parameters:
        user: Saved user instance whose group memberships should be updated.
        explicit_group_names: Optional explicit group names that suppress the generic
            External Agent default for ordinary staff account creation flows.

    Returns:
        Tuple of group names that were added to the user.

    Raises:
        ValueError: If ``user`` has not been saved yet.
    """

    if getattr(user, "pk", None) is None:
        raise ValueError("User must be saved before default staff groups can be ensured.")

    group_names = get_default_staff_group_names(user, explicit_group_names=explicit_group_names)
    if not group_names:
        return ()

    groups_by_name = ensure_security_groups_exist(group_names)
    existing_group_ids = set(user.groups.values_list("pk", flat=True))
    groups_to_add = [group for group in groups_by_name.values() if group.pk not in existing_group_ids]
    if not groups_to_add:
        return ()

    user.groups.add(*groups_to_add)
    return tuple(group.name for group in groups_to_add)
