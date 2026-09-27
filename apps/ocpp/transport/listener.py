"""Classify OCPP listeners using the local interface that accepted the connection."""

import socket
import struct
from collections.abc import Mapping

from django.conf import settings

try:
    import fcntl
except ImportError:  # pragma: no cover - unavailable on Windows
    fcntl = None


SIOCGIFADDR = 0x8915


def trusted_charger_listener(scope: Mapping[str, object]) -> bool:
    """Return whether this connection arrived on the trusted charger interface."""
    interface = settings.OCPP_TRUSTED_CHARGER_INTERFACE
    if not interface:
        return False

    server = scope.get("server")
    if not isinstance(server, (tuple, list)) or not server:
        return False
    local_host = server[0]
    if not isinstance(local_host, str):
        return False

    return local_host in interface_addresses(interface)


def interface_addresses(interface: str) -> set[str]:
    """Return local IPv4 addresses assigned to one interface when available."""
    if fcntl is None:
        return set()

    encoded = interface.encode("utf-8")[:15]
    request = struct.pack("256s", encoded)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            response = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request)
    except OSError:
        return set()
    return {socket.inet_ntoa(response[20:24])}
