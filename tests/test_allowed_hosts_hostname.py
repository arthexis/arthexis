import socket

from django.conf import settings

from config import settings as settings_module


def test_current_hostname_is_allowed():
    hostname = socket.gethostname()
    assert hostname, "socket.gethostname() returned an empty string"
    assert hostname in settings.ALLOWED_HOSTS

def test_mdns_variant_is_generated():
    variants = settings_module._iter_local_hostnames("gway-001", "gway-001.arthexis")
    assert "gway-001" in variants
    assert "gway-001.arthexis" in variants
    assert "gway-001.local" in variants
