from __future__ import annotations

import subprocess
from dataclasses import dataclass
from ipaddress import IPv4Address
from typing import Iterable, Sequence

DEFAULT_TOP_PORTS = 200
DEFAULT_CONSOLE_PORT = 8900
PORT_PREFERENCES: Sequence[int] = (
    DEFAULT_CONSOLE_PORT,
    8443,
    9443,
    443,
    8080,
    8800,
    8000,
    8001,
    8880,
    80,
)
HTTPS_PORTS = {443, 8443, 9443}


@dataclass(frozen=True)
class ConsoleEndpoint:
    host: str
    port: int
    secure: bool = False

    @property
    def url(self) -> str:
        return build_console_url(self.host, self.port, self.secure)


def scan_open_ports(
    host: str,
    *,
    nmap_path: str = "nmap",
    full: bool = False,
    top_ports: int = DEFAULT_TOP_PORTS,
) -> list[int]:
    """Return the list of open TCP ports discovered with nmap.

    The function mirrors the behaviour of the ``evcs_discover`` shell script.
    It uses nmap's grepable output so the caller can avoid touching the
    filesystem and parse the results quickly.
    """

    port_args: list[str]
    if full:
        port_args = ["-p-"]
    else:
        if top_ports <= 0:
            raise ValueError("top_ports must be greater than zero")
        port_args = ["--top-ports", str(top_ports)]

    args = [
        nmap_path,
        "-sS",
        "-Pn",
        "-n",
        "-T4",
        "--open",
        *port_args,
        host,
        "-oG",
        "-",
    ]

    try:
        proc = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    except subprocess.SubprocessError:
        return []

    if proc.returncode != 0:
        return []

    return _parse_nmap_open_ports(proc.stdout)


def _parse_nmap_open_ports(output: str) -> list[int]:
    ports: list[int] = []
    for line in output.splitlines():
        if "Ports:" not in line:
            continue
        try:
            _, ports_section = line.split("Ports:", 1)
        except ValueError:
            continue
        for entry in ports_section.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("/")
            if len(parts) < 2:
                continue
            state = parts[1].strip().lower()
            if state != "open":
                continue
            try:
                port = int(parts[0])
            except ValueError:
                continue
            if port not in ports:
                ports.append(port)
    return ports


def prioritise_ports(ports: Iterable[int]) -> list[int]:
    """Order ports so the most likely console endpoints are tried first."""

    unique = []
    seen = set()
    available = list(dict.fromkeys(ports))
    for preferred in PORT_PREFERENCES:
        if preferred in available and preferred not in seen:
            unique.append(preferred)
            seen.add(preferred)
    for port in available:
        if port not in seen:
            unique.append(port)
            seen.add(port)
    return unique


def select_console_port(ports: Sequence[int]) -> ConsoleEndpoint | None:
    """Pick the best console port from a list of open ports."""

    if not ports:
        return None
    ordered = prioritise_ports(ports)
    if not ordered:
        return None
    port = ordered[0]
    secure = port in HTTPS_PORTS
    return ConsoleEndpoint(host="", port=port, secure=secure)


def build_console_url(host: str, port: int, secure: bool) -> str:
    scheme = "https" if secure else "http"
    host_part = host
    if ":" in host and not host.startswith("["):
        host_part = f"[{host}]"
    return f"{scheme}://{host_part}:{port}"


def normalise_host(host: str | IPv4Address) -> str:
    if isinstance(host, IPv4Address):
        return str(host)
    return str(host)
