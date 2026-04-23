"""Helpers for managing the built-in system user."""

from __future__ import annotations

from typing import Callable, Iterator, Tuple

from django.conf import settings
from django.contrib.auth import get_user_model

from apps.groups.security import ensure_default_staff_groups


SystemUserCheck = Tuple[str, str, Callable[[object], bool]]


_SYSTEM_USER_CHECKS: tuple[SystemUserCheck, ...] = (
    (
        "account is marked as deleted",
        "is_deleted",
        lambda user: getattr(user, "is_deleted", False),
    ),
    ("account is inactive", "is_active", lambda user: not getattr(user, "is_active", True)),
    (
        "account is not marked as staff",
        "is_staff",
        lambda user: not getattr(user, "is_staff", True),
    ),
    (
        "account is not a superuser",
        "is_superuser",
        lambda user: not getattr(user, "is_superuser", True),
    ),
    (
        "account is delegated to another user",
        "operate_as",
        lambda user: getattr(user, "operate_as_id", None),
    ),
    ("account has a usable password", "password", lambda user: user.has_usable_password()),
)


_SYSTEM_USER_FIXERS: dict[str, Callable[[object], None]] = {
    "is_deleted": lambda user: setattr(user, "is_deleted", False),
    "is_active": lambda user: setattr(user, "is_active", True),
    "is_staff": lambda user: setattr(user, "is_staff", True),
    "is_superuser": lambda user: setattr(user, "is_superuser", True),
    "operate_as": lambda user: setattr(user, "operate_as", None),
    "password": lambda user: user.set_unusable_password(),
}


def collect_system_user_issues(user) -> Iterator[str]:
    """Yield a description for each detected system-user issue."""

    for description, _field, predicate in _SYSTEM_USER_CHECKS:
        if predicate(user):
            yield description


def ensure_system_user(*, record_updates: bool = False):
    """Return an ensured system user with no usable password."""

    User = get_user_model()
    username = getattr(User, "SYSTEM_USERNAME", "")
    if not username:
        return None

    manager = getattr(User, "all_objects", User._default_manager)
    user, _created = manager.get_or_create(
        username=username,
        defaults={
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )

    updates: set[str] = set()

    if not user.password:
        user.set_unusable_password()
        updates.add("password")

    for _description, field, predicate in _SYSTEM_USER_CHECKS:
        if predicate(user):
            _SYSTEM_USER_FIXERS[field](user)
            updates.add(field)

    if updates:
        user.save(update_fields=sorted(updates))

    added_groups = ensure_default_staff_groups(user)
    updates.update(f"group:{name}" for name in added_groups)

    if record_updates:
        return user, updates
    return user


def ensure_default_admin_user(
    *,
    username: str | None = None,
    email: str | None = None,
    record_updates: bool = False,
):
    """Return the configured default admin user, creating or repairing it as needed."""

    User = get_user_model()
    resolved_username = (
        str(
            username
            or getattr(settings, "DEFAULT_ADMIN_USERNAME", "")
            or getattr(User, "SYSTEM_USERNAME", "")
            or "arthexis"
        ).strip()
    )
    if not resolved_username:
        return None

    resolved_email = str(
        email if email is not None else getattr(settings, "DEFAULT_ADMIN_EMAIL", "")
    ).strip()

    manager = getattr(User, "all_objects", User._default_manager)
    user, created = manager.get_or_create(
        username=resolved_username,
        defaults={
            "email": resolved_email,
            "is_staff": True,
            "is_superuser": True,
            "is_active": True,
        },
    )

    updates: set[str] = set()
    if created:
        updates.add("created")

    if not user.password:
        user.set_unusable_password()
        updates.add("password")

    if resolved_email and getattr(user, "email", "") != resolved_email:
        user.email = resolved_email
        updates.add("email")
    if getattr(user, "is_deleted", False):
        user.is_deleted = False
        updates.add("is_deleted")
    if not getattr(user, "is_active", True):
        user.is_active = True
        updates.add("is_active")
    if not getattr(user, "is_staff", False):
        user.is_staff = True
        updates.add("is_staff")
    if not getattr(user, "is_superuser", False):
        user.is_superuser = True
        updates.add("is_superuser")
    if getattr(user, "operate_as_id", None):
        user.operate_as = None
        updates.add("operate_as")
    if getattr(user, "allow_local_network_passwordless_login", False):
        user.allow_local_network_passwordless_login = False
        updates.add("allow_local_network_passwordless_login")
    if getattr(user, "temporary_expires_at", None) is not None:
        user.temporary_expires_at = None
        updates.add("temporary_expires_at")

    if updates - {"created"}:
        user.save(update_fields=sorted(updates - {"created"}))

    added_groups = ensure_default_staff_groups(user)
    updates.update(f"group:{name}" for name in added_groups)

    if record_updates:
        return user, updates
    return user
