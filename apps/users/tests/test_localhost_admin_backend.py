import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.users.backends import LocalhostAdminBackend


@pytest.mark.django_db
def test_local_request_creates_admin_user():
    User = get_user_model()
    User.all_objects.filter(username="admin").delete()

    request = RequestFactory().post("/")
    request.META["REMOTE_ADDR"] = "127.0.0.1"

    backend = LocalhostAdminBackend()
    user = backend.authenticate(request, username="admin", password="admin")

    assert user is not None
    assert user.username == "admin"

    admin_user = User.all_objects.get(username="admin")
    assert admin_user.check_password("admin")


@pytest.mark.django_db
def test_remote_request_blocked_and_admin_not_created():
    User = get_user_model()
    User.all_objects.filter(username="admin").delete()

    request = RequestFactory().post("/")
    request.META["REMOTE_ADDR"] = "203.0.113.10"

    backend = LocalhostAdminBackend()
    user = backend.authenticate(request, username="admin", password="admin")

    assert user is None
    assert not User.all_objects.filter(username="admin").exists()


@pytest.mark.django_db
def test_admin_login_creates_system_user_with_unusable_password():
    User = get_user_model()
    User.all_objects.filter(username=User.SYSTEM_USERNAME).delete()
    User.all_objects.filter(username="admin").delete()

    request = RequestFactory().post("/")
    request.META["REMOTE_ADDR"] = "127.0.0.1"

    backend = LocalhostAdminBackend()
    backend.authenticate(request, username="admin", password="admin")

    system_user = User.all_objects.get(username=User.SYSTEM_USERNAME)
    assert system_user.is_staff
    assert system_user.is_superuser
    assert system_user.is_active
    assert not system_user.has_usable_password()
