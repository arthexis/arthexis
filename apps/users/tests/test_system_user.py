import pytest

from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.users import temp_passwords
from apps.users.backends import TempPasswordBackend
from apps.users.system import collect_system_user_issues, ensure_system_user


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
def test_collect_system_user_issues_reports_expected_problems():
    User = get_user_model()
    user = ensure_system_user()

    user.is_deleted = True
    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.operate_as = User.objects.create(username="delegate", is_staff=True)
    user.set_password("secret")
    user.save()

    issues = set(collect_system_user_issues(user))

    assert issues == {
        "account is delegated to another user",
        "account is inactive",
        "account is marked as deleted",
        "account is not a superuser",
        "account is not marked as staff",
        "account has a usable password",
    }


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
def test_temp_password_allows_email_lookup():
    User = get_user_model()
    user = User.objects.create_user(username="email-login", email="email-login@example.com")
    user.set_unusable_password()
    user.save()
    password = temp_passwords.generate_password()
    temp_passwords.store_temp_password(user.username, password)

    backend = TempPasswordBackend()
    request = RequestFactory().post("/")

    authenticated = backend.authenticate(request, username=user.email, password=password)
    assert authenticated is not None
    assert authenticated.pk == user.pk
