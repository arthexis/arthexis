"""Helpers for managing the built-in system user."""

from __future__ import annotations

from django.contrib.auth import get_user_model


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

    if getattr(user, "is_deleted", False):
        user.is_deleted = False
        updates.add("is_deleted")
    if not user.is_active:
        user.is_active = True
        updates.add("is_active")
    if not user.is_staff:
        user.is_staff = True
        updates.add("is_staff")
    if not user.is_superuser:
        user.is_superuser = True
        updates.add("is_superuser")
    if not user.password or user.has_usable_password():
        user.set_unusable_password()
        updates.add("password")
    if getattr(user, "operate_as_id", None):
        user.operate_as = None
        updates.add("operate_as")

    if updates:
        user.save(update_fields=sorted(updates))

    if record_updates:
        return user, updates
    return user

