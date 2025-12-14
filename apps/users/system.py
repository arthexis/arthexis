"""Helpers for managing the built-in system user."""

from __future__ import annotations

from typing import Callable, Iterator, Tuple

from django.contrib.auth import get_user_model


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

    if record_updates:
        return user, updates
    return user

