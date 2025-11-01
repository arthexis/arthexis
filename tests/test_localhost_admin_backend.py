from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.test import override_settings

from core.backends import LocalhostAdminBackend


def ensure_arthexis_user():
    User = get_user_model()
    delegate, created = User.objects.get_or_create(
        username="arthexis",
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


def test_sets_operate_as_on_admin_creation():
    User = get_user_model()
    delegate = ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "127.0.0.1"
    req.META["HTTP_HOST"] = "127.0.0.1"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None
    user.refresh_from_db()
    assert user.username == "admin"
    assert user.operate_as_id == delegate.id


def test_allows_localhost_hostname():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "127.0.0.1"
    req.META["HTTP_HOST"] = "localhost"

    user = backend.authenticate(req, username="admin", password="admin")

    assert user is not None


def test_resets_missing_password_on_login():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    admin = User.objects.create_user(
        username="admin", is_staff=True, is_superuser=True, password="irrelevant"
    )
    admin.password = ""
    admin.save(update_fields=["password"])

    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "127.0.0.1"
    req.META["HTTP_HOST"] = "127.0.0.1"

    user = backend.authenticate(req, username="admin", password="admin")

    assert user is not None
    admin.refresh_from_db()
    assert admin.check_password("admin")


def test_blocks_docker_bridge_addresses():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    User.objects.create_user(
        username="admin", password="admin", is_staff=True, is_superuser=True
    )
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "172.17.0.2"
    req.META["HTTP_HOST"] = "127.0.0.1"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is None


def test_allows_wifi_ap_subnet_via_ip_login():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "10.42.0.20"
    req.META["HTTP_HOST"] = "10.42.0.1"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None


@override_settings(NODE_ROLE="Control")
def test_control_role_allows_private_network():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "10.42.0.15"
    req.META["HTTP_HOST"] = "10.42.0.1"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None


def test_respects_disabled_admin_flag():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    admin = User.objects.create_user(
        username="admin", password="admin", is_staff=True, is_superuser=True
    )
    admin.is_active = False
    admin.save(update_fields=["is_active"])
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "127.0.0.1"
    req.META["HTTP_HOST"] = "127.0.0.1"
    assert backend.authenticate(req, username="admin", password="admin") is None
    admin.refresh_from_db()
    assert admin.is_active is False
    assert User.all_objects.filter(username="admin").count() == 1


def test_hostname_access_is_blocked():
    User = get_user_model()
    ensure_arthexis_user()
    User.all_objects.filter(username="admin").delete()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "127.0.0.1"
    req.META["HTTP_HOST"] = "gway-qk32000"
    assert backend.authenticate(req, username="admin", password="admin") is None
