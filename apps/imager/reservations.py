"""Reservation helpers for Raspberry Pi image builds."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

import psutil
from django.db import IntegrityError, transaction

from apps.nodes.models import Node, NodeRole

DEFAULT_RESERVATION_PORTS = (8888, 80, 443)
RESERVATION_ENV_PATH = "/usr/local/share/arthexis/reserved-node.env"
RESERVATION_JSON_PATH = "/usr/local/share/arthexis/reserved-node.json"
TRUTHY_VALUES = {"1", "true", "yes", "on"}
FALSY_VALUES = {"0", "false", "no", "off"}
HOSTNAME_WITH_NUMBER_RE = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9-]*?)-(?P<number>\d+)$")


@dataclass(frozen=True)
class ImageReservation:
    """Planned node identity for an image before the device first boots."""

    hostname: str
    hostname_prefix: str
    number: int
    ipv4_address: str
    network_cidr: str
    parent_hostname: str
    port: int = 8888
    role_name: str = ""

    def metadata(self) -> dict[str, object]:
        """Return JSON-safe reservation metadata."""

        return asdict(self)


@dataclass(frozen=True)
class ImageReservationCommit:
    """Result of writing a planned image reservation to the node table."""

    node_id: int
    created: bool
    reservation: ImageReservation

    def metadata(self) -> dict[str, object]:
        """Return JSON-safe metadata including the node table row."""

        return {
            "node_id": self.node_id,
            "created": self.created,
            **self.reservation.metadata(),
        }


@dataclass(frozen=True)
class ReservationWatchResult:
    """Single reservation watcher result."""

    node_id: int
    hostname: str
    status: str
    detail: str = ""


def env_bool(name: str, default: bool = False) -> bool:
    """Return a boolean environment value using shell-style truthy strings."""

    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in TRUTHY_VALUES:
        return True
    if raw in FALSY_VALUES:
        return False
    return default


def resolve_optional_env_bool(value: object, env_name: str, *, default: bool = False) -> bool:
    """Resolve an optional CLI boolean with an environment-backed default."""

    if value is None:
        return env_bool(env_name, default)
    return bool(value)


def _clean_hostname_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", value.strip().lower()).strip("-")
    return cleaned or "node"


def default_hostname_prefix() -> str:
    """Return the default hostname prefix for reservations on this originator."""

    env_prefix = (os.environ.get("IMAGER_RESERVE_HOSTNAME_PREFIX") or "").strip()
    if env_prefix:
        return _clean_hostname_prefix(env_prefix)

    local = Node.get_local()
    hostname = (getattr(local, "hostname", "") or socket.gethostname() or "").strip()
    match = HOSTNAME_WITH_NUMBER_RE.match(hostname)
    if match:
        return _clean_hostname_prefix(match.group("prefix"))
    return _clean_hostname_prefix(hostname)


def _existing_numbers_for_prefix(prefix: str) -> set[int]:
    numbers: set[int] = set()
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$", re.IGNORECASE)
    for hostname in Node.objects.filter(hostname__istartswith=f"{prefix}-").values_list(
        "hostname", flat=True
    ):
        match = pattern.match(hostname or "")
        if match:
            numbers.add(int(match.group(1)))
    return numbers


def next_reservation_number(prefix: str) -> int:
    """Return the next hostname number for a reservation prefix."""

    numbers = _existing_numbers_for_prefix(prefix)
    return max(numbers, default=0) + 1


def _interface_networks() -> list[ipaddress.IPv4Network]:
    env_network = (os.environ.get("IMAGER_RESERVE_NETWORK_CIDR") or "").strip()
    if env_network:
        try:
            return [ipaddress.ip_network(env_network, strict=False)]
        except ValueError:
            return []

    networks: list[tuple[int, ipaddress.IPv4Network]] = []
    for name, addresses in psutil.net_if_addrs().items():
        for addr in addresses:
            if getattr(addr.family, "name", "") != "AF_INET":
                continue
            if not addr.address or not addr.netmask:
                continue
            try:
                interface = ipaddress.ip_interface(f"{addr.address}/{addr.netmask}")
            except ValueError:
                continue
            if interface.ip.is_loopback or interface.ip.is_link_local:
                continue
            priority = 10
            if name.startswith("wlan"):
                priority = 0
            elif name == "eth0":
                priority = 1
            elif interface.ip.is_private:
                priority = 5
            networks.append((priority, interface.network))
    ordered: list[ipaddress.IPv4Network] = []
    for _priority, network in sorted(networks, key=lambda item: (item[0], str(item[1]))):
        if network not in ordered:
            ordered.append(network)
    return ordered


def _known_neighbor_ips() -> set[str]:
    ip_path = shutil_which("ip")
    if not ip_path:
        return set()
    try:
        result = subprocess.run(
            [ip_path, "-4", "neigh", "show"],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.5,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    values: set[str] = set()
    for line in result.stdout.splitlines():
        token = line.split(maxsplit=1)[0] if line.split() else ""
        try:
            values.add(str(ipaddress.ip_address(token)))
        except ValueError:
            continue
    return values


def shutil_which(command: str) -> str | None:
    """Small wrapper to keep command discovery patchable in tests."""

    from shutil import which

    return which(command)


def _used_ipv4_addresses() -> set[str]:
    used: set[str] = set(_known_neighbor_ips())
    for node in Node.objects.all().only("address", "ipv4_address"):
        for raw_value in (node.address, node.ipv4_address):
            for token in re.split(r"[\s,]+", raw_value or ""):
                if not token:
                    continue
                try:
                    address = ipaddress.ip_address(token)
                except ValueError:
                    continue
                if address.version == 4:
                    used.add(str(address))
    for addresses in psutil.net_if_addrs().values():
        for addr in addresses:
            if getattr(addr.family, "name", "") == "AF_INET" and addr.address:
                used.add(addr.address)
    return used


def _candidate_number_address(
    network: ipaddress.IPv4Network,
    number: int,
) -> ipaddress.IPv4Address | None:
    if number <= 0:
        return None
    candidate = ipaddress.ip_address(int(network.network_address) + number)
    if candidate in network and candidate not in {network.network_address, network.broadcast_address}:
        return candidate
    return None


def choose_free_ipv4_address(number: int | None = None) -> tuple[str, str]:
    """Choose a currently unassigned IPv4 address on a preferred local network."""

    networks = _interface_networks()
    used = _used_ipv4_addresses()
    for network in networks:
        if number is not None:
            numbered = _candidate_number_address(network, number)
            if numbered and str(numbered) not in used:
                return str(numbered), str(network)
        for address in network.hosts():
            value = str(address)
            if value not in used:
                return value, str(network)
    return "", ""


def plan_image_reservation(
    *,
    hostname_prefix: str = "",
    number: int | None = None,
    role_name: str = "",
) -> ImageReservation:
    """Build a reservation plan without writing it to the database."""

    prefix = _clean_hostname_prefix(hostname_prefix) if hostname_prefix else default_hostname_prefix()
    if number is not None and number <= 0:
        raise ValueError("Reservation number must be greater than zero.")
    resolved_number = number or next_reservation_number(prefix)
    ipv4_address, network_cidr = choose_free_ipv4_address(resolved_number)
    hostname = f"{prefix}-{resolved_number:03d}"
    parent = Node.get_local()
    return ImageReservation(
        hostname=hostname,
        hostname_prefix=prefix,
        number=resolved_number,
        ipv4_address=ipv4_address,
        network_cidr=network_cidr,
        parent_hostname=(getattr(parent, "hostname", "") or socket.gethostname() or "").strip(),
        role_name=(role_name or "").strip(),
    )


def commit_image_reservation(reservation: ImageReservation) -> ImageReservationCommit:
    """Create or update the reserved peer row for an image reservation."""

    role = (
        NodeRole.objects.filter(name=reservation.role_name).first()
        if reservation.role_name
        else None
    )
    defaults: dict[str, Any] = {
        "address": reservation.ipv4_address,
        "ipv4_address": reservation.ipv4_address,
        "network_hostname": reservation.hostname,
        "port": reservation.port,
        "current_relation": Node.Relation.PEER,
        "reserved": True,
    }
    if role:
        defaults["role"] = role

    with transaction.atomic():
        node = Node.objects.filter(hostname__iexact=reservation.hostname).first()
        created = False
        if node is None:
            node = Node.objects.create(hostname=reservation.hostname, **defaults)
            created = True
        else:
            update_fields: list[str] = []
            for field, value in defaults.items():
                if getattr(node, field) != value:
                    setattr(node, field, value)
                    update_fields.append(field)
            if update_fields:
                node.save(update_fields=update_fields)
    return ImageReservationCommit(node_id=node.id, created=created, reservation=reservation)


def render_reservation_env(reservation: ImageReservation) -> str:
    """Render a shell environment file that makes first boot use the reservation hostname."""

    lines = [
        f"NODE_HOSTNAME={shlex.quote(reservation.hostname)}",
        f"NODE_RESERVED_HOSTNAME={shlex.quote(reservation.hostname)}",
    ]
    if reservation.ipv4_address:
        lines.append(f"NODE_RESERVED_IPV4={shlex.quote(reservation.ipv4_address)}")
    return "\n".join(lines) + "\n"


def render_reservation_json(reservation: ImageReservation) -> str:
    """Render JSON metadata baked into the generated image."""

    return json.dumps(reservation.metadata(), indent=2, sort_keys=True) + "\n"


def active_parent_network_names() -> list[str]:
    """Return active Wi-Fi NetworkManager connection names on the originator."""

    nmcli = shutil_which("nmcli")
    if not nmcli:
        return []
    try:
        result = subprocess.run(
            [nmcli, "-t", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active"],
            capture_output=True,
            text=True,
            check=False,
            timeout=3.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []

    names: list[str] = []
    for raw_line in result.stdout.splitlines():
        parts = raw_line.split(":")
        if len(parts) < 3:
            continue
        name, connection_type, device = parts[0], parts[1], parts[2]
        if connection_type == "wifi" and device.startswith("wlan") and name not in names:
            names.append(name)
    return names


def watch_interfaces_from_env() -> list[str]:
    """Return configured reservation-watch interfaces or discover wlanX plus eth0."""

    raw = (os.environ.get("IMAGER_RESERVATION_WATCH_INTERFACES") or "").strip()
    if raw:
        return [token.strip() for token in raw.split(",") if token.strip()]
    stats = psutil.net_if_stats()
    return [
        name
        for name, stat in stats.items()
        if stat.isup and (name.startswith("wlan") or name == "eth0")
    ]


def _known_interface_hosts(interface_name: str) -> set[str]:
    ip_path = shutil_which("ip")
    if not ip_path:
        return set()
    try:
        result = subprocess.run(
            [ip_path, "-4", "neigh", "show", "dev", interface_name],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    hosts: set[str] = set()
    for line in result.stdout.splitlines():
        token = line.split(maxsplit=1)[0] if line.split() else ""
        try:
            hosts.add(str(ipaddress.ip_address(token)))
        except ValueError:
            continue
    return hosts


def _node_candidate_hosts(node: Node, interfaces: list[str]) -> list[str]:
    hosts: list[str] = []
    for raw in (node.address, node.network_hostname, node.hostname, node.ipv4_address):
        for token in re.split(r"[\s,]+", raw or ""):
            token = token.strip()
            if token and token not in hosts:
                hosts.append(token)
    for interface in interfaces:
        for host in sorted(_known_interface_hosts(interface)):
            if host not in hosts:
                hosts.append(host)
    return hosts


def _fetch_node_info(host: str, ports: tuple[int, ...], timeout: float) -> dict[str, Any] | None:
    for port in ports:
        schemes = ("https",) if port == 443 else ("http", "https")
        for scheme in schemes:
            url = f"{scheme}://{host}:{port}/nodes/info/"
            request = Request(url, headers={"User-Agent": "arthexis-reservation-watch/1.0"})
            try:
                with urlopen(request, timeout=timeout) as response:
                    if response.status != 200:
                        continue
                    payload = json.loads(response.read(8192).decode("utf-8"))
            except (OSError, URLError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("hostname"):
                payload["_watch_host"] = host
                payload["_watch_port"] = port
                return payload
    return None


def _info_matches_reservation(node: Node, info: dict[str, Any]) -> bool:
    expected_hostname = (node.hostname or "").strip().lower()
    reported_hostname = str(info.get("hostname") or "").strip().lower()
    if expected_hostname:
        return reported_hostname == expected_hostname
    candidates = {
        token
        for raw in (node.address, node.ipv4_address, node.network_hostname)
        for token in re.split(r"[\s,]+", raw or "")
        if token
    }
    return bool(str(info.get("_watch_host") or "") in candidates)


def confirm_reserved_node(node: Node, info: dict[str, Any]) -> ReservationWatchResult:
    """Apply discovered node information to a reservation and clear its flag."""

    role = None
    role_name = str(info.get("role") or info.get("role_name") or "").strip()
    if role_name:
        role = NodeRole.objects.filter(name=role_name).first()
    mac_address = str(info.get("mac_address") or "").strip().lower()
    if mac_address and Node.objects.filter(mac_address=mac_address).exclude(pk=node.pk).exists():
        return ReservationWatchResult(
            node_id=node.id,
            hostname=node.hostname,
            status="conflict",
            detail=f"MAC address already belongs to another node: {mac_address}",
        )

    fields = {
        "hostname": str(info.get("hostname") or node.hostname).strip(),
        "network_hostname": str(info.get("network_hostname") or "").strip(),
        "address": str(info.get("address") or info.get("_watch_host") or "").strip(),
        "ipv4_address": ",".join(Node.sanitize_ipv4_addresses(info.get("ipv4_address") or [])),
        "ipv6_address": str(info.get("ipv6_address") or "").strip(),
        "host_instance_id": str(info.get("host_instance_id") or "").strip(),
        "port": int(info.get("port") or 8888),
        "installed_version": str(info.get("installed_version") or "")[:20],
        "installed_revision": str(info.get("installed_revision") or "")[:40],
        "public_key": str(info.get("public_key") or ""),
        "mac_address": mac_address,
        "current_relation": Node.Relation.PEER,
        "trusted": True,
        "reserved": False,
    }
    if role:
        fields["role"] = role
    update_fields: list[str] = []
    for field, value in fields.items():
        if getattr(node, field) != value:
            setattr(node, field, value)
            update_fields.append(field)
    if update_fields:
        try:
            node.save(update_fields=update_fields)
        except IntegrityError as exc:
            return ReservationWatchResult(
                node_id=node.id,
                hostname=node.hostname,
                status="error",
                detail=str(exc),
            )
    return ReservationWatchResult(
        node_id=node.id,
        hostname=node.hostname,
        status="confirmed",
        detail=f"{fields['address']}:{fields['port']}",
    )


def watch_reserved_nodes_once(
    *,
    interfaces: list[str] | None = None,
    ports: tuple[int, ...] = DEFAULT_RESERVATION_PORTS,
    timeout: float = 1.5,
) -> list[ReservationWatchResult]:
    """Probe reserved nodes and clear reservations that respond as expected."""

    selected_interfaces = interfaces if interfaces is not None else watch_interfaces_from_env()
    results: list[ReservationWatchResult] = []
    for node in Node.objects.filter(reserved=True).order_by("hostname", "id"):
        candidates = _node_candidate_hosts(node, selected_interfaces)
        matched = False
        for host in candidates:
            info = _fetch_node_info(host, ports, timeout)
            if not info or not _info_matches_reservation(node, info):
                continue
            results.append(confirm_reserved_node(node, info))
            matched = True
            break
        if not matched:
            results.append(
                ReservationWatchResult(
                    node_id=node.id,
                    hostname=node.hostname,
                    status="pending",
                    detail="no matching /nodes/info/ response",
                )
            )
    return results


def watch_reserved_nodes_loop(
    *,
    interfaces: list[str] | None = None,
    ports: tuple[int, ...] = DEFAULT_RESERVATION_PORTS,
    timeout: float = 1.5,
    interval: float = 30.0,
):
    """Yield watcher results forever at a fixed interval."""

    while True:
        yield watch_reserved_nodes_once(
            interfaces=interfaces,
            ports=ports,
            timeout=timeout,
        )
        time.sleep(interval)
