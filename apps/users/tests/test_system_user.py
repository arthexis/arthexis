import io

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory

from apps.users import temp_passwords
from apps.users.backends import TempPasswordBackend
from apps.users.system import ensure_system_user


@pytest.mark.django_db
def test_ensure_system_user_creates_and_repairs_account():
    User = get_user_model()
    User.all_objects.filter(username=User.SYSTEM_USERNAME).delete()

    user, created_updates = ensure_system_user(record_updates=True)
    assert user.username == User.SYSTEM_USERNAME
    assert "password" in created_updates
    assert user.is_staff and user.is_superuser and user.is_active
    assert not user.has_usable_password()
    assert user.operate_as_id is None

    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.operate_as = User.objects.create(username="delegate", is_staff=True)
    user.set_password("secret")
    user.save()

    repaired_user, updated = ensure_system_user(record_updates=True)
    assert repaired_user.pk == user.pk
    assert {"is_active", "is_staff", "is_superuser", "password", "operate_as"}.issubset(
        updated
    )
    assert repaired_user.is_active and repaired_user.is_staff and repaired_user.is_superuser
    assert repaired_user.operate_as_id is None
    assert not repaired_user.has_usable_password()


@pytest.mark.django_db
def test_system_user_only_authenticates_with_temp_password():
    User = get_user_model()
    user = ensure_system_user()
    backend = TempPasswordBackend()
    request = RequestFactory().post("/")

    temp_passwords.discard_temp_password(user.username)
    assert backend.authenticate(request, username=user.username, password="wrong") is None

    password = temp_passwords.generate_password()
    temp_passwords.store_temp_password(user.username, password)

    authenticated = backend.authenticate(request, username=user.username, password=password)
    assert authenticated is not None
    assert authenticated.username == user.username

    assert (
        backend.authenticate(request, username=user.username, password="incorrect") is None
    )


@pytest.mark.django_db
def test_temp_password_command_creates_system_user_when_missing():
    User = get_user_model()
    User.all_objects.filter(username=User.SYSTEM_USERNAME).delete()

    out = io.StringIO()
    call_command("temp_password", User.SYSTEM_USERNAME, stdout=out)

    output = out.getvalue()
    assert f"Temporary password for {User.SYSTEM_USERNAME}:" in output

    user = User.all_objects.get(username=User.SYSTEM_USERNAME)
    assert not user.has_usable_password()
    assert temp_passwords.load_temp_password(user.username) is not None
