import ipaddress

from django.http import HttpRequest
from django.contrib.auth import get_user_model

from accounts.backends import LocalhostAdminBackend


def test_docker_network_allowed(tmp_path):
    # Ensure the default admin exists
    User = get_user_model()
    assert User.objects.filter(username="admin").exists()
    backend = LocalhostAdminBackend()
    req = HttpRequest()
    req.META["REMOTE_ADDR"] = "172.16.5.4"
    user = backend.authenticate(req, username="admin", password="admin")
    assert user is not None
