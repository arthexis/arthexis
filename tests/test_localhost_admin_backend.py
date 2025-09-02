import ipaddress

from django.http import HttpRequest
from django.contrib.auth import get_user_model

from core.backends import LocalhostAdminBackend


def test_docker_network_allowed(tmp_path):
    User = get_user_model()
    User.objects.create_user(
        username="admin", password="admin", is_staff=True, is_superuser=True
    )
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "172.16.5.4"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None
