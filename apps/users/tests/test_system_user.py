import pytest

from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.groups.constants import (
    NETWORK_OPERATOR_GROUP_NAME,
    PRODUCT_DEVELOPER_GROUP_NAME,
    RELEASE_MANAGER_GROUP_NAME,
)
from apps.users import temp_passwords
from apps.users.backends import TempPasswordBackend
from apps.users.system import (
    collect_system_user_issues,
    ensure_default_admin_user,
    ensure_system_user,
)


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
    assert user.groups.filter(name=NETWORK_OPERATOR_GROUP_NAME).exists()
    assert user.groups.filter(name=PRODUCT_DEVELOPER_GROUP_NAME).exists()
    assert user.groups.filter(name=RELEASE_MANAGER_GROUP_NAME).exists()

    user.is_active = False
    user.is_staff = False
    user.is_superuser = False
    user.operate_as = User.objects.create(username="delegate", is_staff=True)
    user.set_password("secret")
    user.save()
    user.groups.clear()

    repaired_user, updated = ensure_system_user(record_updates=True)
    assert repaired_user.pk == user.pk
    assert {
        "group:Network Operator",
        "group:Product Developer",
        "group:Release Manager",
        "is_active",
        "is_staff",
        "is_superuser",
        "password",
        "operate_as",
    }.issubset(updated)
    assert (
        repaired_user.is_active
        and repaired_user.is_staff
        and repaired_user.is_superuser
    )
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
    assert (
        backend.authenticate(request, username=user.username, password="wrong") is None
    )

    password = temp_passwords.generate_password()
    temp_passwords.store_temp_password(user.username, password)

    authenticated = backend.authenticate(
        request, username=user.username, password=password
    )
    assert authenticated is not None
    assert authenticated.username == user.username

    assert (
        backend.authenticate(request, username=user.username, password="incorrect")
        is None
    )


@pytest.mark.django_db
def test_ensure_default_admin_user_uses_configured_defaults(settings):
    settings.DEFAULT_ADMIN_USERNAME = "ops-admin"
    settings.DEFAULT_ADMIN_EMAIL = "tecnologia@gelectriic.com"

    User = get_user_model()
    delegate = User.objects.create(username="delegate-admin", is_staff=True)
    existing = User.all_objects.create_user(
        username="ops-admin",
        email="wrong@example.com",
        is_active=False,
        is_staff=False,
        is_superuser=False,
        allow_local_network_passwordless_login=True,
    )
    existing.is_deleted = True
    existing.operate_as = delegate
    existing.save()

    user, updates = ensure_default_admin_user(record_updates=True)

    assert user.pk == existing.pk
    assert user.username == "ops-admin"
    assert user.email == "tecnologia@gelectriic.com"
    assert user.is_active is True
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_deleted is False
    assert user.allow_local_network_passwordless_login is False
    assert user.operate_as_id is None
    assert "email" in updates
    assert "is_active" in updates
    assert "is_staff" in updates
    assert "is_superuser" in updates


@pytest.mark.django_db
def test_ensure_default_admin_user_creates_unusable_password_account(settings):
    settings.DEFAULT_ADMIN_USERNAME = "ops-admin"
    settings.DEFAULT_ADMIN_EMAIL = "tecnologia@gelectriic.com"

    User = get_user_model()
    User.all_objects.filter(username="ops-admin").delete()

    user, updates = ensure_default_admin_user(record_updates=True)

    assert user.username == "ops-admin"
    assert user.email == "tecnologia@gelectriic.com"
    assert user.is_active is True
    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.has_usable_password() is False
    assert "created" in updates
    assert "password" in updates
