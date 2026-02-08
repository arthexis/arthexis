"""Network validation helpers for simulators."""

from __future__ import annotations

import ipaddress
import socket
from typing import Iterable


def _iter_host_ips(host: str, ws_port: int | None) -> Iterable[str]:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        port = int(ws_port) if ws_port else 0
        for _, _, _, _, sockaddr in socket.getaddrinfo(
            host, port, type=socket.SOCK_STREAM
        ):
            yield sockaddr[0]
    else:
        yield host


def validate_simulator_endpoint(
    host: str,
    ws_port: int | None,
    *,
    allow_private_network: bool = False,
) -> None:
    """Validate that the simulator endpoint is safe to connect to."""

    if not host or not str(host).strip():
        raise ValueError("Simulator host is required.")

    if ws_port is not None:
        try:
            port = int(ws_port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Simulator port must be an integer.") from exc
        if port < 1 or port > 65535:
            raise ValueError("Simulator port must be between 1 and 65535.")

    try:
        addresses = list(_iter_host_ips(host, ws_port))
    except OSError as exc:
        raise ValueError("Unable to resolve simulator host.") from exc
    if not addresses:
        raise ValueError("Unable to resolve simulator host.")

    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_unspecified or ip.is_multicast:
            raise ValueError("Simulator host resolves to a disallowed address.")
        if not allow_private_network and not ip.is_global:
            raise ValueError("Simulator host resolves to a private address.")


__all__ = ["validate_simulator_endpoint"]
