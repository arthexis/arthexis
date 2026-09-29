"""Passive network observations and CSMS candidate correlation."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from apps.ocpp.discovery.projection import project_discovery_event
from apps.ocpp.discovery.session import DiscoverySession

_ALLOWED_ROLES = frozenset({"control", "satellite"})
_TSHARK_FIELDS = (
    "frame.time_epoch",
    "eth.src",
    "eth.dst",
    "arp.opcode",
    "arp.src.proto_ipv4",
    "arp.dst.proto_ipv4",
    "arp.src.hw_mac",
    "ip.src",
    "ip.dst",
    "ipv6.src",
    "ipv6.dst",
    "icmpv6.type",
    "icmpv6.nd.ns.target_address",
    "icmpv6.nd.na.target_address",
    "icmpv6.opt.src_linkaddr",
    "icmpv6.opt.target_linkaddr",
    "tcp.srcport",
    "tcp.dstport",
    "tcp.flags.syn",
    "tcp.flags.ack",
    "dns.id",
    "dns.flags.response",
    "dns.qry.type",
    "dns.qry.name",
    "dns.a",
    "dns.aaaa",
    "dhcp.option.dhcp",
    "dhcp.option.requested_ip_address",
    "dhcp.ip.your",
    "dhcp.hw.mac_addr",
    "http.request.method",
    "http.host",
    "http.request.uri",
    "http.upgrade",
    "http.sec_websocket_protocol",
    "tls.handshake.type",
    "tls.handshake.extensions_server_name",
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
    http_paths: set[str] = field(default_factory=set)
    websocket_paths: set[str] = field(default_factory=set)
    ocpp_subprotocols: set[str] = field(default_factory=set)
    tls_sni: set[str] = field(default_factory=set)
    attempts: int = 0
    first_attempt_at: float | None = None
    last_attempt_at: float | None = None
    retry_intervals_seconds: list[float] = field(default_factory=list)
    mac_without_resolution: bool = False

    def as_metadata(self) -> dict[str, object]:
        """Return deterministic machine-readable candidate metadata."""

        return {
            "destination_ip": self.destination_ip,
            "destination_port": self.destination_port,
            "destination_mac": self.destination_mac,
            "hostnames": sorted(self.hostnames),
            "http_paths": sorted(self.http_paths),
            "websocket_paths": sorted(self.websocket_paths),
            "ocpp_subprotocols": sorted(self.ocpp_subprotocols),
            "tls_sni": sorted(self.tls_sni),
            "attempts": self.attempts,
            "first_attempt_at": self.first_attempt_at,
            "last_attempt_at": self.last_attempt_at,
            "retry_intervals_seconds": self.retry_intervals_seconds,
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


class TsharkObserver:
    """Read structured passive packet fields from the native TShark capability."""

    def __init__(self, executable: str | None = None) -> None:
        self.executable = executable or shutil.which("tshark")

    def link_state(self, interface: str) -> str | None:
        """Return passive Linux link-state evidence when available."""
        state_path = Path("/sys/class/net") / interface / "operstate"
        try:
            return state_path.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def preflight(self, interface: str) -> None:
        """Require TShark and a non-empty interface before session creation."""

        if not interface.strip():
            raise DiscoveryPreflightError("A charger-facing interface is required.")
        if not self.executable:
            raise DiscoveryPreflightError(
                "Passive charger discovery requires the native packet-analysis "
                "capability (TShark/Wireshark CLI). Provision the Control/Satellite "
                "Box profile or install the required native capture dependency."
            )

    def observations(self, interface: str) -> Iterator[NetworkObservation]:
        """Stream selected TShark fields; never transmit or mutate networking."""

        command = [
            self.executable,
            "-l",
            "-n",
            "-i",
            interface,
            "-T",
            "fields",
            "-E",
            "separator=/t",
            "-E",
            "occurrence=f",
        ]
        for field_name in _TSHARK_FIELDS:
            command.extend(["-e", field_name])

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
                observation = parse_tshark_line(line)
                if observation is not None:
                    yield observation
        finally:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def parse_tshark_line(line: str) -> NetworkObservation | None:
    """Map one tab-separated TShark fields row into structured evidence."""

    values = line.rstrip("\n").split("\t")
    values.extend([""] * (len(_TSHARK_FIELDS) - len(values)))
    fields = dict(zip(_TSHARK_FIELDS, values, strict=False))

    timestamp = None
    try:
        timestamp = float(fields["frame.time_epoch"])
    except ValueError:
        pass
    source_mac = fields["eth.src"].lower() or None
    destination_mac = fields["eth.dst"].lower() or None
    arp_opcode = fields["arp.opcode"]

    if arp_opcode == "1":
        return NetworkObservation(
            "arp_request",
            {
                "sender_ip": fields["arp.src.proto_ipv4"] or None,
                "target_ip": fields["arp.dst.proto_ipv4"] or None,
                "source_mac": source_mac,
                "destination_mac": destination_mac,
            },
        )

    if arp_opcode == "2":
        return NetworkObservation(
            "arp_reply",
            {
                "sender_ip": fields["arp.src.proto_ipv4"] or None,
                "sender_mac": (fields["arp.src.hw_mac"].lower() or source_mac),
                "source_mac": source_mac,
                "destination_mac": destination_mac,
            },
        )

    source_ip = fields["ip.src"] or fields["ipv6.src"]
    destination_ip = fields["ip.dst"] or fields["ipv6.dst"]
    source_port = fields["tcp.srcport"]
    destination_port = fields["tcp.dstport"]
    tcp_syn = fields["tcp.flags.syn"] == "1"
    tcp_ack = fields["tcp.flags.ack"] == "1"
    dns_id = fields["dns.id"]
    dns_response = fields["dns.flags.response"]
    dns_name = fields["dns.qry.name"].rstrip(".")
    dns_type = fields["dns.qry.type"]
    dns_address = fields["dns.a"] or fields["dns.aaaa"]
    dhcp_message_type = fields["dhcp.option.dhcp"]
    http_method = fields["http.request.method"]
    http_host = fields["http.host"]
    http_uri = fields["http.request.uri"]
    http_upgrade = fields["http.upgrade"]
    websocket_protocol = fields["http.sec_websocket_protocol"]
    tls_handshake_type = fields["tls.handshake.type"]
    tls_server_name = fields["tls.handshake.extensions_server_name"]

    metadata: dict[str, object] = {
        "source_ip": source_ip or None,
        "source_port": int(source_port) if source_port.isdigit() else None,
        "destination_ip": destination_ip or None,
        "destination_port": (
            int(destination_port) if destination_port.isdigit() else None
        ),
        "source_mac": source_mac,
        "destination_mac": destination_mac,
        "observed_at": timestamp,
    }

    icmpv6_type = fields["icmpv6.type"]
    if icmpv6_type in {"135", "136"}:
        target = (
            fields["icmpv6.nd.ns.target_address"]
            if icmpv6_type == "135"
            else fields["icmpv6.nd.na.target_address"]
        )
        metadata.update(
            {
                "target_ip": target or None,
                "source_link_address": fields["icmpv6.opt.src_linkaddr"] or None,
                "target_link_address": fields["icmpv6.opt.target_linkaddr"] or None,
            }
        )
        return NetworkObservation(
            "ndp_neighbor_solicitation"
            if icmpv6_type == "135"
            else "ndp_neighbor_advertisement",
            metadata,
        )

    if dhcp_message_type.isdigit():
        metadata.update(
            {
                "message_type": int(dhcp_message_type),
                "requested_address": fields["dhcp.option.requested_ip_address"] or None,
                "offered_address": fields["dhcp.ip.your"] or None,
                "client_mac": fields["dhcp.hw.mac_addr"].lower() or source_mac,
            }
        )
        return NetworkObservation("dhcp", metadata)

    if dns_id.isdigit() and dns_response == "0" and dns_name:
        metadata.update(
            {
                "dns_id": int(dns_id),
                "record_type": {"1": "A", "28": "AAAA"}.get(dns_type, dns_type),
                "name": dns_name,
                "client_ip": source_ip,
                "dns_server": destination_ip,
            }
        )
        return NetworkObservation("dns_query", metadata)

    if dns_id.isdigit() and dns_response == "1" and dns_address:
        metadata.update(
            {
                "dns_id": int(dns_id),
                "address": dns_address,
                "client_ip": destination_ip,
                "dns_server": source_ip,
            }
        )
        return NetworkObservation("dns_response", metadata)

    if (
        tls_handshake_type == "1"
        and destination_ip
        and destination_port.isdigit()
    ):
        metadata["sni"] = tls_server_name or None
        return NetworkObservation("tls_client_hello", metadata)

    if http_method and destination_ip and destination_port.isdigit():
        metadata.update(
            {
                "method": http_method,
                "hostname": http_host or None,
                "path": http_uri or None,
            }
        )
        if http_upgrade.casefold() == "websocket":
            metadata["subprotocol"] = websocket_protocol or None
            return NetworkObservation("websocket_upgrade", metadata)
        return NetworkObservation("http_request", metadata)

    if destination_ip and destination_port.isdigit() and tcp_syn and not tcp_ack:
        return NetworkObservation("connection_attempt", metadata)
    if destination_ip and destination_port.isdigit():
        return NetworkObservation("traffic_observed", metadata)
    return None


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
        if observation.event_type in {"arp_reply", "ndp_neighbor_advertisement"}:
            ip = (
                metadata.get("sender_ip")
                if observation.event_type == "arp_reply"
                else metadata.get("target_ip")
            )
            mac = (
                metadata.get("sender_mac")
                if observation.event_type == "arp_reply"
                else metadata.get("target_link_address")
            )
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
            "http_request",
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
        if observation.event_type == "connection_attempt":
            observed_at = metadata.get("observed_at")
            if isinstance(observed_at, (int, float)):
                timestamp = float(observed_at)
                if candidate.first_attempt_at is None:
                    candidate.first_attempt_at = timestamp
                if candidate.last_attempt_at is not None:
                    candidate.retry_intervals_seconds.append(
                        round(timestamp - candidate.last_attempt_at, 6)
                    )
                candidate.last_attempt_at = timestamp
            candidate.attempts += 1
        candidate.hostnames.update(self._dns_resolutions.get(destination_ip, set()))

        derived: list[NetworkObservation] = []
        destination_mac = metadata.get("destination_mac")
        if isinstance(destination_mac, str):
            candidate.destination_mac = destination_mac
            resolved = self._resolved_neighbors.get(destination_ip)
            if resolved != destination_mac:
                candidate.mac_without_resolution = True
                derived.append(
                    NetworkObservation(
                        "destination_mac_observed_without_resolution",
                        {
                            "destination_ip": destination_ip,
                            "destination_port": destination_port,
                            "destination_mac": destination_mac,
                        },
                    )
                )

        hostname = metadata.get("hostname")
        if isinstance(hostname, str):
            candidate.hostnames.add(hostname)
        path = metadata.get("path")
        if isinstance(path, str):
            candidate.http_paths.add(path)
            if observation.event_type == "websocket_upgrade":
                candidate.websocket_paths.add(path)
        subprotocol = metadata.get("subprotocol")
        if (
            observation.event_type == "websocket_upgrade"
            and isinstance(subprotocol, str)
        ):
            candidate.ocpp_subprotocols.add(subprotocol)
        sni = metadata.get("sni")
        if isinstance(sni, str):
            candidate.tls_sni.add(sni)
        return derived

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

    session = DiscoverySession.create(root, projector=project_discovery_event)
    session.record(
        "interface_selected",
        metadata={"interface": interface, "role": normalized_role},
    )
    link_state = getattr(observer, "link_state", None)
    if callable(link_state):
        state = link_state(interface)
        if state:
            session.record(
                "link_state",
                metadata={"interface": interface, "state": state},
            )

    tracker = CandidateTracker()

    try:
        for observation in observer.observations(interface):
            session.record(observation.event_type, metadata=observation.metadata)
            for derived in tracker.consume(observation):
                session.record(derived.event_type, metadata=derived.metadata)
    except KeyboardInterrupt:
        session.record(
            "session_interrupted",
            metadata={"reason": "operator_interrupt"},
        )

    for candidate in tracker.candidates():
        session.record(
            "csms_candidate",
            category="inference",
            metadata=candidate.as_metadata(),
        )

    session.write_summary()
    return session
