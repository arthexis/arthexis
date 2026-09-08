"""Health checks owned by the users domain."""

from __future__ import annotations

import io
from collections.abc import Iterable
from typing import Protocol, cast

from django.contrib.auth import get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.core.management.base import CommandError

from apps.groups.security import ensure_default_staff_groups
from apps.users.system import collect_system_user_issues, ensure_system_user


class SupportsNaturalKeyManager(Protocol):
    """Manager protocol for natural-key user lookups used by health checks."""

    def get_by_natural_key(self, username: str) -> AbstractBaseUser:
        """Return the user matching ``username``."""


def _get_user_by_natural_key(
    user_model: type[AbstractBaseUser], username: str
) -> AbstractBaseUser | None:
    manager = cast(
        SupportsNaturalKeyManager,
        getattr(user_model, "all_objects", user_model._default_manager),
    )
    try:
        return manager.get_by_natural_key(username)
    except user_model.DoesNotExist:
        return None


def _collect_admin_issues(user) -> Iterable[str]:
    if getattr(user, "is_deleted", False):
        yield "account is marked as deleted"
    if not getattr(user, "is_active", True):
        yield "account is inactive"
    if not getattr(user, "is_staff", True):
        yield "account is not marked as staff"
    if not getattr(user, "is_superuser", True):
        yield "account is not a superuser"
    if not user.password:
        yield "account does not have a password"
    elif not user.has_usable_password():
        yield "account password is unusable"


def _resolve_system_delegate(user):
    user_model = type(user)
    system_username = getattr(user_model, "SYSTEM_USERNAME", "")
    if not system_username:
        return None
    manager = getattr(user_model, "all_objects", user_model._default_manager)
    delegate = manager.filter(username=system_username).exclude(pk=user.pk).first()
    if delegate is None:
        return None
    if not getattr(delegate, "is_staff", True) or not getattr(
        delegate, "is_superuser", True
    ):
        return None
    return delegate


def _repair_admin(user) -> set[str]:
    updated: set[str] = set()
    if getattr(user, "is_deleted", False):
        user.is_deleted = False
        updated.add("is_deleted")
    if not getattr(user, "is_active", True):
        user.is_active = True
        updated.add("is_active")
    if not getattr(user, "is_staff", True):
        user.is_staff = True
        updated.add("is_staff")
    if not getattr(user, "is_superuser", True):
        user.is_superuser = True
        updated.add("is_superuser")
    delegate = _resolve_system_delegate(user)
    if delegate is not None and user.operate_as_id in {None, user.pk}:
        user.operate_as = delegate
        updated.add("operate_as")
    if (
        not user.password
        or not user.has_usable_password()
        or not user.check_password("admin")
    ):
        user.set_password("admin")
        updated.add("password")
    return updated


def _create_admin(user_model, username):
    user = user_model.all_objects.create(
        username=username,
        is_staff=True,
        is_superuser=True,
        is_active=True,
    )
    user.set_password("admin")
    delegate = _resolve_system_delegate(user)
    if delegate is not None:
        user.operate_as = delegate
    user.save()

    from apps.locals.models import ensure_admin_favorites

    ensure_admin_favorites(user)
    ensure_default_staff_groups(user)
    return user


def run_check_admin(*, stdout, style, force: bool = False, **_kwargs) -> None:
    """Validate that the default admin account is available."""

    user_model = get_user_model()
    username = getattr(user_model, "ADMIN_USERNAME", "admin")
    if not username:
        raise CommandError("The user model does not define an admin username.")

    user = _get_user_by_natural_key(user_model, username)

    if user is None:
        if not force:
            raise CommandError(
                f"No account exists for username {username!r}. Use --force to create it."
            )
        _create_admin(user_model, username)
        stdout.write(style.SUCCESS(f"Created default admin account {username!r}."))
        return

    issues = list(_collect_admin_issues(user))
    if issues and not force:
        buffer = io.StringIO()
        buffer.write(
            f"Issues detected with the {username!r} account. Use --force to repair it.\n"
        )
        for issue in issues:
            buffer.write(f" - {issue}\n")
        raise CommandError(buffer.getvalue().rstrip())

    if force:
        updated = _repair_admin(user)
        if updated:
            user.save(update_fields=sorted(updated))
        added_groups = ensure_default_staff_groups(user)
        if updated or added_groups:
            changes = sorted(updated) + [f"group:{name}" for name in added_groups]
            stdout.write(
                style.SUCCESS(
                    f"Repaired default admin account {username!r}: {', '.join(changes)}."
                )
            )
        else:
            stdout.write(
                style.SUCCESS(f"Default admin account {username!r} is already healthy.")
            )
        return

    stdout.write(style.SUCCESS(f"Default admin account {username!r} is healthy."))


def run_check_system_user(*, stdout, style, force: bool = False, **_kwargs) -> None:
    """Validate that the system account is available and secured."""

    user_model = get_user_model()
    username = getattr(user_model, "SYSTEM_USERNAME", "")
    if not username:
        raise CommandError("The user model does not define a system username.")

    user = _get_user_by_natural_key(user_model, username)

    if user is None:
        if not force:
            raise CommandError(
                f"No account exists for username {username!r}. Use --force to create it."
            )
        ensure_system_user()
        stdout.write(style.SUCCESS(f"Created system account {username!r}."))
        return

    issues = list(collect_system_user_issues(user))
    if issues and not force:
        buffer = io.StringIO()
        buffer.write(
            f"Issues detected with the {username!r} account. Use --force to repair it.\n"
        )
        for issue in issues:
            buffer.write(f" - {issue}\n")
        raise CommandError(buffer.getvalue().rstrip())

    if force:
        _user, updated = ensure_system_user(record_updates=True)
        if updated:
            stdout.write(
                style.SUCCESS(
                    f"Repaired system account {username!r}: {', '.join(sorted(updated))}."
                )
            )
        else:
            stdout.write(
                style.SUCCESS(f"System account {username!r} is already healthy.")
            )
        return

    stdout.write(style.SUCCESS(f"System account {username!r} is healthy."))
