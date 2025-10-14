from __future__ import annotations

import io

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError


pytestmark = pytest.mark.django_db


def ensure_system_user():
    User = get_user_model()
    delegate, created = User.objects.get_or_create(
        username=getattr(User, "SYSTEM_USERNAME", "arthexis"),
        defaults={
            "email": "arthexis@example.com",
            "is_staff": True,
            "is_superuser": True,
        },
    )
    changed = False
    if not delegate.is_staff:
        delegate.is_staff = True
        changed = True
    if not delegate.is_superuser:
        delegate.is_superuser = True
        changed = True
    if not delegate.has_usable_password():
        delegate.set_password("arthexis")
        changed = True
    if created or changed:
        delegate.save()
    return delegate


def _admin_username():
    User = get_user_model()
    return getattr(User, "ADMIN_USERNAME", "admin")


def test_check_admin_reports_success_when_account_valid():
    User = get_user_model()
    username = _admin_username()
    User.all_objects.filter(username=username).delete()
    User.objects.create_user(
        username=username,
        password="admin",
        is_staff=True,
        is_superuser=True,
    )

    output = io.StringIO()
    call_command("check_admin", stdout=output)

    message = output.getvalue().lower()
    assert "healthy" in message


def test_check_admin_errors_when_missing():
    User = get_user_model()
    username = _admin_username()
    User.all_objects.filter(username=username).delete()

    with pytest.raises(CommandError) as excinfo:
        call_command("check_admin")

    assert username in str(excinfo.value)


def test_check_admin_force_creates_account():
    User = get_user_model()
    username = _admin_username()
    User.all_objects.filter(username=username).delete()
    ensure_system_user()

    output = io.StringIO()
    call_command("check_admin", force=True, stdout=output)

    user = User.all_objects.get(username=username)
    assert user.is_staff
    assert user.is_superuser
    assert user.is_active
    assert user.check_password("admin")
    assert user.operate_as_id is not None

    message = output.getvalue().lower()
    assert "created" in message


def test_check_admin_force_repairs_account():
    User = get_user_model()
    username = _admin_username()
    User.all_objects.filter(username=username).delete()
    delegate = ensure_system_user()
    user = User.all_objects.create(
        username=username,
        is_staff=False,
        is_superuser=False,
        is_active=False,
    )
    user.is_deleted = True
    user.password = ""
    user.save(update_fields=["is_deleted", "password"])

    output = io.StringIO()
    call_command("check_admin", force=True, stdout=output)

    user.refresh_from_db()
    assert user.is_staff
    assert user.is_superuser
    assert user.is_active
    assert not user.is_deleted
    assert user.check_password("admin")
    assert user.operate_as_id == delegate.pk

    message = output.getvalue().lower()
    assert "repaired" in message
