from django.http import HttpRequest
from django.contrib.auth import get_user_model

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
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None
    user.refresh_from_db()
    assert user.username == "admin"
    assert user.operate_as_id == delegate.id


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
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is None
