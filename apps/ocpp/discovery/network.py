"""Passive network observations and CSMS candidate correlation."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from apps.ocpp.discovery.session import DiscoverySession

_ALLOWED_ROLES = frozenset({"control", "satellite"})
_MAC = r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}"
_IP = r"(?:\d{1,3}\.){3}\d{1,3}"
_ETHERNET_RE = re.compile(
    rf"^(?P<timestamp>\d+(?:\.\d+)?)\s+"
    rf"(?P<src_mac>{_MAC})\s+>\s+(?P<dst_mac>{_MAC}),"
)
_FLOW_RE = re.compile(
    rf"(?P<src_ip>{_IP})\.(?P<src_port>\d+)\s+>\s+"
    rf"(?P<dst_ip>{_IP})\.(?P<dst_port>\d+):"
)
_ARP_REQUEST_RE = re.compile(
    rf"Request who-has (?P<target_ip>{_IP}) tell (?P<sender_ip>{_IP})"
)
_ARP_REPLY_RE = re.compile(
    rf"Reply (?P<sender_ip>{_IP}) is-at (?P<sender_mac>{_MAC})"
)
_DNS_QUERY_RE = re.compile(
    r":\s+(?P<dns_id>\d+)\+?\s+.*?\b(?P<record_type>A|AAAA)\?\s+"
    r"(?P<name>[A-Za-z0-9_.-]+)\.?"
)
_DNS_RESPONSE_RE = re.compile(
    rf":\s+(?P<dns_id>\d+)\s+.*?\bA\s+(?P<address>{_IP})(?:\s|$)"
)


class DiscoveryPreflightError(RuntimeError):
    """Friendly operator-facing passive-discovery preflight failure."""


@dataclass(frozen=True)
class NetworkObservation:
    """One passive network fact emitted by an observer."""

    event_type: str
    metadata: Mapping[str, object]


@dataclass
class CsmsCandidate:
    """Correlated passive evidence for one charger destination."""

    destination_ip: str
    destination_port: int
    destination_mac: str | None = None
    hostnames: set[str] = field(default_factory=set)
    websocket_paths: set[str] = field(default_factory=set)
    ocpp_subprotocols: set[str] = field(default_factory=set)
    tls_sni: set[str] = field(default_factory=set)
    attempts: int = 0
    mac_without_resolution: bool = False

    def as_metadata(self) -> dict[str, object]:
        """Return deterministic machine-readable candidate metadata."""

        return {
            "destination_ip": self.destination_ip,
            "destination_port": self.destination_port,
            "destination_mac": self.destination_mac,
            "hostnames": sorted(self.hostnames),
            "websocket_paths": sorted(self.websocket_paths),
            "ocpp_subprotocols": sorted(self.ocpp_subprotocols),
            "tls_sni": sorted(self.tls_sni),
            "attempts": self.attempts,
            "destination_mac_observed_without_resolution": (
                self.mac_without_resolution
            ),
        }


class PassiveObserver(Protocol):
    """Passive-only source of structured network observations."""

    def preflight(self, interface: str) -> None:
        """Fail before a discovery session starts when observation is unavailable."""

    def observations(self, interface: str) -> Iterable[NetworkObservation]:
        """Yield passive observations without initiating charger traffic."""


class TcpdumpObserver:
    """Read passive Ethernet metadata from the native tcpdump capability."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("tcpdump")

    def preflight(self, interface: str) -> None:
        """Require tcpdump and a non-empty interface before session creation."""

        if not interface.strip():
            raise DiscoveryPreflightError("A charger-facing interface is required.")
        if not self.executable:
            raise DiscoveryPreflightError(
                "Passive charger discovery requires the native packet-capture "
                "capability (tcpdump). Provision the Control/Satellite Box profile "
                "or install the required native capture dependency."
            )

    def observations(self, interface: str) -> Iterator[NetworkObservation]:
        """Stream metadata from tcpdump; never transmit or mutate networking."""

        command = [
            self.executable,
            "-l",
            "-nn",
            "-e",
            "-tt",
            "-vv",
            "-i",
            interface,
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        try:
            for line in process.stdout:
                observation = parse_tcpdump_line(line)
                if observation is not None:
                    yield observation
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def parse_tcpdump_line(line: str) -> NetworkObservation | None:
    """Parse the passive metadata we can safely retain from one tcpdump line."""

    ethernet = _ETHERNET_RE.search(line)
    source_mac = ethernet.group("src_mac").lower() if ethernet else None
    destination_mac = ethernet.group("dst_mac").lower() if ethernet else None

    request = _ARP_REQUEST_RE.search(line)
    if request:
        return NetworkObservation(
            "arp_request",
            {
                "sender_ip": request.group("sender_ip"),
                "target_ip": request.group("target_ip"),
                "source_mac": source_mac,
                "destination_mac": destination_mac,
            },
        )

    reply = _ARP_REPLY_RE.search(line)
    if reply:
        return NetworkObservation(
            "arp_reply",
            {
                "sender_ip": reply.group("sender_ip"),
                "sender_mac": reply.group("sender_mac").lower(),
                "source_mac": source_mac,
                "destination_mac": destination_mac,
            },
        )

    flow = _FLOW_RE.search(line)
    if not flow:
        return None

    metadata: dict[str, object] = {
        "source_ip": flow.group("src_ip"),
        "source_port": int(flow.group("src_port")),
        "destination_ip": flow.group("dst_ip"),
        "destination_port": int(flow.group("dst_port")),
        "source_mac": source_mac,
        "destination_mac": destination_mac,
    }
    dns_query = _DNS_QUERY_RE.search(line)
    if dns_query:
        metadata.update(
            {
                "dns_id": int(dns_query.group("dns_id")),
                "record_type": dns_query.group("record_type"),
                "name": dns_query.group("name").rstrip("."),
                "client_ip": flow.group("src_ip"),
                "dns_server": flow.group("dst_ip"),
            }
        )
        return NetworkObservation("dns_query", metadata)

    dns_response = _DNS_RESPONSE_RE.search(line)
    if dns_response and int(flow.group("src_port")) == 53:
        metadata.update(
            {
                "dns_id": int(dns_response.group("dns_id")),
                "address": dns_response.group("address"),
                "client_ip": flow.group("dst_ip"),
                "dns_server": flow.group("src_ip"),
            }
        )
        return NetworkObservation("dns_response", metadata)

    return NetworkObservation("connection_attempt", metadata)


class CandidateTracker:
    """Correlate passive observations without turning inference into evidence."""

    def __init__(self) -> None:
        self._resolved_neighbors: dict[str, str] = {}
        self._pending_dns: dict[tuple[str, str, int], str] = {}
        self._dns_resolutions: dict[str, set[str]] = {}
        self._candidates: dict[tuple[str, int], CsmsCandidate] = {}

    def consume(
        self, observation: NetworkObservation
    ) -> list[NetworkObservation]:
        """Consume evidence and return any additional derived observations."""

        metadata = observation.metadata
        if observation.event_type == "arp_reply":
            ip = metadata.get("sender_ip")
            mac = metadata.get("sender_mac")
            if isinstance(ip, str) and isinstance(mac, str):
                self._resolved_neighbors[ip] = mac
            return []

        if observation.event_type == "dns_query":
            name = metadata.get("name")
            client_ip = metadata.get("client_ip")
            dns_server = metadata.get("dns_server")
            dns_id = metadata.get("dns_id")
            if (
                isinstance(name, str)
                and isinstance(client_ip, str)
                and isinstance(dns_server, str)
                and isinstance(dns_id, int)
            ):
                self._pending_dns[(client_ip, dns_server, dns_id)] = name
            return []

        if observation.event_type == "dns_response":
            client_ip = metadata.get("client_ip")
            dns_server = metadata.get("dns_server")
            dns_id = metadata.get("dns_id")
            address = metadata.get("address")
            if (
                isinstance(client_ip, str)
                and isinstance(dns_server, str)
                and isinstance(dns_id, int)
                and isinstance(address, str)
            ):
                name = self._pending_dns.pop((client_ip, dns_server, dns_id), None)
                if name is not None:
                    self._dns_resolutions.setdefault(address, set()).add(name)
                    return [
                        NetworkObservation(
                            "dns_resolution",
                            {
                                "name": name,
                                "address": address,
                                "dns_server": dns_server,
                                "dns_id": dns_id,
                            },
                        )
                    ]
            return []

        if observation.event_type not in {
            "connection_attempt",
            "websocket_upgrade",
            "tls_client_hello",
        }:
            return []

        destination_ip = metadata.get("destination_ip")
        destination_port = metadata.get("destination_port")
        if not isinstance(destination_ip, str) or not isinstance(
            destination_port, int
        ):
            return []

        key = (destination_ip, destination_port)
        candidate = self._candidates.setdefault(
            key,
            CsmsCandidate(destination_ip, destination_port),
        )
        candidate.attempts += 1
        candidate.hostnames.update(self._dns_resolutions.get(destination_ip, set()))

        destination_mac = metadata.get("destination_mac")
        if isinstance(destination_mac, str):
            candidate.destination_mac = destination_mac
            resolved = self._resolved_neighbors.get(destination_ip)
            if resolved != destination_mac:
                candidate.mac_without_resolution = True
                return [
                    NetworkObservation(
                        "destination_mac_observed_without_resolution",
                        {
                            "destination_ip": destination_ip,
                            "destination_port": destination_port,
                            "destination_mac": destination_mac,
                        },
                    )
                ]

        hostname = metadata.get("hostname")
        if isinstance(hostname, str):
            candidate.hostnames.add(hostname)
        path = metadata.get("path")
        if isinstance(path, str):
            candidate.websocket_paths.add(path)
        subprotocol = metadata.get("subprotocol")
        if isinstance(subprotocol, str):
            candidate.ocpp_subprotocols.add(subprotocol)
        sni = metadata.get("sni")
        if isinstance(sni, str):
            candidate.tls_sni.add(sni)
        return []

    def candidates(self) -> list[CsmsCandidate]:
        """Return candidates ranked by observed connection attempts."""

        return sorted(
            self._candidates.values(),
            key=lambda candidate: (
                -candidate.attempts,
                candidate.destination_ip,
                candidate.destination_port,
            ),
        )


def run_passive_discovery(
    *,
    interface: str,
    role: str | None,
    root: Path,
    observer: PassiveObserver,
) -> DiscoverySession:
    """Run foreground passive discovery and persist evidence before inference."""

    normalized_role = (role or "").strip().casefold()
    if normalized_role not in _ALLOWED_ROLES:
        raise DiscoveryPreflightError(
            "Charger discovery is supported only on Control and Satellite nodes."
        )

    observer.preflight(interface)

    session = DiscoverySession.create(root)
    session.record(
        "interface_selected",
        metadata={"interface": interface, "role": normalized_role},
    )
    tracker = CandidateTracker()

    for observation in observer.observations(interface):
        session.record(observation.event_type, metadata=observation.metadata)
        for derived in tracker.consume(observation):
            session.record(derived.event_type, metadata=derived.metadata)

    for candidate in tracker.candidates():
        session.record(
            "csms_candidate",
            category="inference",
            metadata=candidate.as_metadata(),
        )

    session.write_summary()
    return session
