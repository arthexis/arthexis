"""Reservation helpers for Raspberry Pi image builds."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

import psutil
from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.nodes.models import Node, NodeRole

DEFAULT_RESERVATION_PORTS = (8888, 80, 443)
DEFAULT_GWAY_REGISTRATION_BASE_URL = ""
GWAY_HOSTNAME_PREFIX = "gway"
NEXT_GWAY_NUMBER_PATH = "/nodes/register/next-gway-number/"
RESERVATION_ENV_PATH = "/usr/local/share/arthexis/reserved-node.env"
RESERVATION_JSON_PATH = "/usr/local/share/arthexis/reserved-node.json"
REMOTE_NEXT_NUMBER_TIMEOUT_SECONDS = 5.0
TRUTHY_VALUES = {"1", "true", "yes", "on"}
FALSY_VALUES = {"0", "false", "no", "off"}
HOSTNAME_WITH_NUMBER_RE = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9-]*?)-(?P<number>\d+)$")
GWAY_RESERVATION_TOKEN_ENV_NAMES = (
    "IMAGER_GWAY_RESERVATION_TOKEN",
    "ARTHEXIS_GWAY_RESERVATION_TOKEN",
)
RESERVATION_CLAIM_TOKEN_HASH_KEY = "reservation_claim_token_hash"


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
    downstream_registration_base_url: str = ""
    claim_token: str = ""

    def metadata(self) -> dict[str, object]:
        """Return JSON-safe reservation metadata."""

        data = asdict(self)
        claim_token = data.pop("claim_token", "")
        data["claim_token_baked"] = bool(claim_token)
        return data


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


@dataclass(frozen=True)
class RemoteReservation:
    """Reserved number and claim token returned by an upstream server."""

    number: int
    claim_token: str = ""


class RemoteReservationError(RuntimeError):
    """Raised when a configured upstream reservation cannot be completed."""


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


def clean_registration_base_url(value: str) -> str:
    """Return a normalized registration base URL without a trailing slash."""

    cleaned = (value or "").strip()
    if not cleaned:
        return ""
    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"
    return cleaned.rstrip("/")


def default_gway_next_number_base_url() -> str:
    """Return the configured optional upstream base URL for GWAY number lookup."""

    return clean_registration_base_url(
        os.environ.get("IMAGER_GWAY_REGISTRATION_BASE_URL")
        or DEFAULT_GWAY_REGISTRATION_BASE_URL
    )


def default_gway_downstream_registration_base_url() -> str:
    """Return the configured optional upstream base URL for first-boot registration."""

    return clean_registration_base_url(
        os.environ.get("IMAGER_DOWNSTREAM_REGISTRATION_BASE_URL")
        or DEFAULT_GWAY_REGISTRATION_BASE_URL
    )


def default_gway_reservation_token() -> str:
    """Return the configured bearer token for upstream GWAY number reservations."""

    for name in GWAY_RESERVATION_TOKEN_ENV_NAMES:
        token = (os.environ.get(name) or "").strip()
        if token:
            return token
    return ""


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


def _remote_reservation_query(
    prefix: str, minimum_number: int | None
) -> dict[str, str]:
    query = {"prefix": prefix}
    if minimum_number is not None and minimum_number > 0:
        query["minimum_number"] = str(minimum_number)
    return query


def _remote_reservation_from_payload(payload: object) -> RemoteReservation | None:
    if not isinstance(payload, dict):
        return None
    number = _remote_reservation_number_from_payload(payload)
    claim_token = str(
        payload.get("claim_token") or payload.get("reservation_claim_token") or ""
    ).strip()
    if number <= 0 or not claim_token:
        return None
    return RemoteReservation(number=number, claim_token=claim_token)


def _remote_reservation_number_from_payload(payload: dict) -> int:
    for key in ("next_number", "number"):
        try:
            number = int(payload.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return 0


def remote_next_reservation(
    prefix: str,
    *,
    base_url: str = "",
    minimum_number: int | None = None,
    reservation_token: str = "",
    timeout: float = REMOTE_NEXT_NUMBER_TIMEOUT_SECONDS,
) -> RemoteReservation | None:
    """Return the next reservation advertised by an upstream registration server."""

    normalized_base = clean_registration_base_url(base_url)
    if not normalized_base:
        return None
    url = urljoin(
        normalized_base.rstrip("/") + "/",
        NEXT_GWAY_NUMBER_PATH.lstrip("/"),
    )
    headers = {
        "User-Agent": "arthexis-imager-gway-reservation/1.0",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    token = (reservation_token or default_gway_reservation_token()).strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url,
        data=urlencode(_remote_reservation_query(prefix, minimum_number)).encode(
            "utf-8"
        ),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return _remote_reservation_from_payload(payload)


def remote_next_reservation_number(
    prefix: str,
    *,
    base_url: str = "",
    minimum_number: int | None = None,
    reservation_token: str = "",
    timeout: float = REMOTE_NEXT_NUMBER_TIMEOUT_SECONDS,
) -> int | None:
    """Return the next number advertised by an upstream registration server."""

    reservation = remote_next_reservation(
        prefix,
        base_url=base_url,
        minimum_number=minimum_number,
        reservation_token=reservation_token,
        timeout=timeout,
    )
    return reservation.number if reservation else None


def next_reservation(
    prefix: str,
    *,
    remote_base_url: str = "",
    remote_reservation_token: str = "",
    timeout: float = REMOTE_NEXT_NUMBER_TIMEOUT_SECONDS,
) -> RemoteReservation:
    """Return the next hostname reservation for a reservation prefix."""

    numbers = _existing_numbers_for_prefix(prefix)
    local_next = max(numbers, default=0) + 1
    normalized_remote_base_url = clean_registration_base_url(remote_base_url)
    if not normalized_remote_base_url:
        return RemoteReservation(number=local_next)
    remote_reservation = remote_next_reservation(
        prefix,
        base_url=normalized_remote_base_url,
        minimum_number=local_next,
        reservation_token=remote_reservation_token,
        timeout=timeout,
    )
    if remote_reservation is None:
        raise RemoteReservationError(
            "Could not reserve a GWAY number from the configured upstream server. "
            "Configure IMAGER_GWAY_RESERVATION_TOKEN or ARTHEXIS_GWAY_RESERVATION_TOKEN, "
            "or use a manual --reserve-number for an offline reservation."
        )
    return remote_reservation


def next_reservation_number(
    prefix: str,
    *,
    remote_base_url: str = "",
    remote_reservation_token: str = "",
    timeout: float = REMOTE_NEXT_NUMBER_TIMEOUT_SECONDS,
) -> int:
    """Return the next hostname number for a reservation prefix."""

    return next_reservation(
        prefix,
        remote_base_url=remote_base_url,
        remote_reservation_token=remote_reservation_token,
        timeout=timeout,
    ).number


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


def _collect_neighbor_ips(
    interface_name: str | None = None, *, timeout: float = 1.5
) -> set[str]:
    ip_path = shutil_which("ip")
    if not ip_path:
        return set()
    command = [ip_path, "-4", "neigh", "show"]
    if interface_name:
        command.extend(["dev", interface_name])
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
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


def _known_neighbor_ips() -> set[str]:
    return _collect_neighbor_ips()


def shutil_which(command: str) -> str | None:
    """Small wrapper to keep command discovery patchable in tests."""

    from shutil import which

    return which(command)


def _used_ipv4_addresses(*, exclude_node_ids: set[int] | None = None) -> set[str]:
    used: set[str] = set(_known_neighbor_ips())
    exclude_ips: set[str] = set()
    if exclude_node_ids:
        for node in Node.objects.filter(id__in=exclude_node_ids).only(
            "address",
            "ipv4_address",
        ):
            for raw_value in (node.address, node.ipv4_address):
                for token in re.split(r"[\s,]+", raw_value or ""):
                    if not token:
                        continue
                    try:
                        address = ipaddress.ip_address(token)
                    except ValueError:
                        continue
                    if address.version == 4:
                        exclude_ips.add(str(address))
    used.difference_update(exclude_ips)
    queryset = Node.objects.all().only("id", "address", "ipv4_address")
    if exclude_node_ids:
        queryset = queryset.exclude(id__in=exclude_node_ids)
    for node in queryset:
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

    return choose_free_ipv4_address_excluding(number)


def choose_free_ipv4_address_excluding(
    number: int | None = None,
    *,
    exclude_node_ids: set[int] | None = None,
) -> tuple[str, str]:
    """Choose an IPv4 address while allowing callers to rebuild an existing reservation."""

    networks = _interface_networks()
    used = _used_ipv4_addresses(exclude_node_ids=exclude_node_ids)
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


def _reserved_node_role_name(node: Node | None) -> str:
    if node is None or not getattr(node, "role_id", None):
        return ""
    role = getattr(node, "role", None)
    return (getattr(role, "name", "") or "").strip()


def plan_image_reservation(
    *,
    hostname_prefix: str = "",
    number: int | None = None,
    role_name: str = "",
    next_number_base_url: str = "",
    downstream_registration_base_url: str = "",
    claim_token: str = "",
) -> ImageReservation:
    """Build a reservation plan without writing it to the database."""

    prefix = _clean_hostname_prefix(hostname_prefix) if hostname_prefix else default_hostname_prefix()
    if number is not None and number <= 0:
        raise ValueError("Reservation number must be greater than zero.")
    remote_reservation = None
    if number is None:
        remote_reservation = next_reservation(
            prefix,
            remote_base_url=next_number_base_url,
        )
        resolved_number = remote_reservation.number
    else:
        resolved_number = number
    hostname = f"{prefix}-{resolved_number:03d}"
    existing_reserved_node = (
        Node.objects.select_related("role")
        .filter(hostname__iexact=hostname, reserved=True)
        .first()
    )
    exclude_node_ids = (
        {existing_reserved_node.id} if existing_reserved_node is not None else None
    )
    ipv4_address, network_cidr = choose_free_ipv4_address_excluding(
        resolved_number,
        exclude_node_ids=exclude_node_ids,
    )
    parent = Node.get_local()
    registration_base_url = clean_registration_base_url(
        downstream_registration_base_url
    )
    resolved_claim_token = (
        (claim_token or "").strip()
        or (remote_reservation.claim_token if remote_reservation else "")
        or secrets.token_urlsafe(32)
    )
    return ImageReservation(
        hostname=hostname,
        hostname_prefix=prefix,
        number=resolved_number,
        ipv4_address=ipv4_address,
        network_cidr=network_cidr,
        parent_hostname=(getattr(parent, "hostname", "") or socket.gethostname() or "").strip(),
        role_name=(role_name or _reserved_node_role_name(existing_reserved_node)).strip(),
        downstream_registration_base_url=registration_base_url,
        claim_token=resolved_claim_token,
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
        "mesh_key_fingerprint_metadata": {
            RESERVATION_CLAIM_TOKEN_HASH_KEY: make_password(reservation.claim_token)
        }
        if reservation.claim_token
        else {},
    }
    if role:
        defaults["role"] = role

    with transaction.atomic():
        node = (
            Node.objects.select_for_update()
            .filter(hostname__iexact=reservation.hostname)
            .first()
        )
        created = False
        if node is None:
            node = Node.objects.create(hostname=reservation.hostname, **defaults)
            created = True
        else:
            if not node.reserved:
                raise ValueError(
                    f"Reservation hostname is already used by active node: {reservation.hostname}"
                )
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
    if reservation.role_name:
        lines.append(f"NODE_ROLE={shlex.quote(reservation.role_name)}")
    if reservation.ipv4_address:
        lines.append(f"NODE_RESERVED_IPV4={shlex.quote(reservation.ipv4_address)}")
    if reservation.claim_token:
        lines.append(
            f"NODE_RESERVED_CLAIM_TOKEN={shlex.quote(reservation.claim_token)}"
        )
    if reservation.downstream_registration_base_url:
        lines.append(
            "ARTHEXIS_DOWNSTREAM_REGISTRATION_BASE_URL="
            f"{shlex.quote(reservation.downstream_registration_base_url)}"
        )
    return "\n".join(lines) + "\n"


def render_reservation_json(reservation: ImageReservation) -> str:
    """Render JSON metadata baked into the generated image."""

    return json.dumps(reservation.metadata(), indent=2, sort_keys=True) + "\n"


def active_parent_network_names() -> list[str]:
    """Return active upstream NetworkManager connection names on the originator."""

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
        if not name or not device:
            continue
        if connection_type not in {"802-3-ethernet", "wifi"}:
            continue
        if name.startswith("arthexis-"):
            continue
        if name not in names:
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
    return _collect_neighbor_ips(interface_name, timeout=1.0)


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


def _url_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _fetch_node_info(host: str, ports: tuple[int, ...], timeout: float) -> dict[str, Any] | None:
    for port in ports:
        schemes = ("https",) if port == 443 else ("http", "https")
        for scheme in schemes:
            url = f"{scheme}://{_url_host(host)}:{port}/nodes/info/"
            request = Request(url, headers={"User-Agent": "arthexis-reservation-watch/1.0"})
            try:
                with urlopen(request, timeout=timeout) as response:
                    if response.status != 200:
                        continue
                    payload = json.loads(response.read())
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and payload.get("hostname"):
                payload["_watch_host"] = host
                payload["_watch_port"] = port
                return payload
    return None


def _node_info_port(info: dict[str, Any]) -> int:
    for raw_value in (info.get("port"), info.get("_watch_port"), 8888):
        if raw_value in (None, ""):
            continue
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            continue
    return 8888


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


def observe_reserved_node(node: Node, info: dict[str, Any]) -> ReservationWatchResult:
    """Report a matching reservation candidate without trusting the peer."""

    mac_address = str(info.get("mac_address") or "").strip().lower()
    if mac_address and Node.objects.filter(mac_address=mac_address).exclude(pk=node.pk).exists():
        return ReservationWatchResult(
            node_id=node.id,
            hostname=node.hostname,
            status="conflict",
            detail=f"MAC address already belongs to another node: {mac_address}",
        )
    address = str(info.get("address") or info.get("_watch_host") or "").strip()
    port = _node_info_port(info)
    return ReservationWatchResult(
        node_id=node.id,
        hostname=node.hostname,
        status="observed",
        detail=f"{address}:{port} matched; waiting for signed registration",
    )


def watch_reserved_nodes_once(
    *,
    interfaces: list[str] | None = None,
    ports: tuple[int, ...] = DEFAULT_RESERVATION_PORTS,
    timeout: float = 1.5,
) -> list[ReservationWatchResult]:
    """Probe reserved nodes and report peers that still need signed registration."""

    selected_interfaces = interfaces if interfaces is not None else watch_interfaces_from_env()
    results: list[ReservationWatchResult] = []
    for node in Node.objects.filter(reserved=True).order_by("hostname", "id"):
        candidates = _node_candidate_hosts(node, selected_interfaces)
        matched = False
        for host in candidates:
            info = _fetch_node_info(host, ports, timeout)
            if not info or not _info_matches_reservation(node, info):
                continue
            results.append(observe_reserved_node(node, info))
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
