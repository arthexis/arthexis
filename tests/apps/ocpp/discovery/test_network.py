import pytest

from apps.ocpp.discovery.network import (
    CandidateTracker,
    DiscoveryPreflightError,
    NetworkObservation,
    TsharkObserver,
    parse_tshark_line,
    run_passive_discovery,
)



_TSHARK_FIELD_ORDER = (
    "eth.src",
    "eth.dst",
    "arp.opcode",
    "arp.src.proto_ipv4",
    "arp.dst.proto_ipv4",
    "arp.src.hw_mac",
    "ip.src",
    "ip.dst",
    "tcp.srcport",
    "tcp.dstport",
    "dns.id",
    "dns.flags.response",
    "dns.qry.type",
    "dns.qry.name",
    "dns.a",
    "http.request.method",
    "http.host",
    "http.request.uri",
    "http.upgrade",
    "http.sec_websocket_protocol",
    "tls.handshake.type",
    "tls.handshake.extensions_server_name",
)


def tshark_row(**fields) -> str:
    return "\t".join(str(fields.get(name, "")) for name in _TSHARK_FIELD_ORDER)


class FakeObserver:
    def __init__(self, observations=(), error: Exception | None = None) -> None:
        self.items = list(observations)
        self.error = error
        self.preflight_calls: list[str] = []
        self.observe_calls: list[str] = []

    def preflight(self, interface: str) -> None:
        self.preflight_calls.append(interface)
        if self.error is not None:
            raise self.error

    def observations(self, interface: str):
        self.observe_calls.append(interface)
        yield from self.items


def test_role_gate_runs_before_observer_or_session_creation(tmp_path) -> None:
    observer = FakeObserver()

    with pytest.raises(
        DiscoveryPreflightError,
        match="only on Control and Satellite",
    ):
        run_passive_discovery(
            interface="eth0",
            role="watchtower",
            root=tmp_path,
            observer=observer,
        )

    assert observer.preflight_calls == []
    assert observer.observe_calls == []
    assert not (tmp_path / "discovery").exists()


def test_dependency_preflight_runs_before_session_creation(tmp_path) -> None:
    observer = FakeObserver(error=DiscoveryPreflightError("capture unavailable"))

    with pytest.raises(DiscoveryPreflightError, match="capture unavailable"):
        run_passive_discovery(
            interface="eth0",
            role="control",
            root=tmp_path,
            observer=observer,
        )

    assert observer.preflight_calls == ["eth0"]
    assert observer.observe_calls == []
    assert not (tmp_path / "discovery").exists()


def test_passive_discovery_correlates_ip_port_and_mac_without_arp(tmp_path) -> None:
    observer = FakeObserver(
        [
            NetworkObservation(
                "connection_attempt",
                {
                    "source_ip": "192.0.2.20",
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "source_mac": "00:11:22:33:44:55",
                    "destination_mac": "aa:bb:cc:dd:ee:ff",
                },
            ),
            NetworkObservation(
                "connection_attempt",
                {
                    "source_ip": "192.0.2.20",
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "source_mac": "00:11:22:33:44:55",
                    "destination_mac": "aa:bb:cc:dd:ee:ff",
                },
            ),
        ]
    )

    session = run_passive_discovery(
        interface="eth0",
        role="satellite",
        root=tmp_path,
        observer=observer,
    )

    events = session.events()
    assert observer.observe_calls == ["eth0"]
    missing_resolution = [
        event
        for event in events
        if event["event_type"] == "destination_mac_observed_without_resolution"
    ]
    assert len(missing_resolution) == 2
    candidate = next(
        event for event in events if event["event_type"] == "csms_candidate"
    )
    assert candidate["category"] == "inference"
    assert candidate["metadata"]["destination_ip"] == "198.51.100.40"
    assert candidate["metadata"]["destination_port"] == 9000
    assert candidate["metadata"]["destination_mac"] == "aa:bb:cc:dd:ee:ff"
    assert candidate["metadata"]["attempts"] == 2
    assert (
        candidate["metadata"]["destination_mac_observed_without_resolution"]
        is True
    )


def test_arp_reply_prevents_false_cached_neighbor_observation(tmp_path) -> None:
    observer = FakeObserver(
        [
            NetworkObservation(
                "arp_reply",
                {
                    "sender_ip": "198.51.100.40",
                    "sender_mac": "aa:bb:cc:dd:ee:ff",
                },
            ),
            NetworkObservation(
                "connection_attempt",
                {
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "destination_mac": "aa:bb:cc:dd:ee:ff",
                },
            ),
        ]
    )

    session = run_passive_discovery(
        interface="eth0",
        role="control",
        root=tmp_path,
        observer=observer,
    )

    assert not any(
        event["event_type"] == "destination_mac_observed_without_resolution"
        for event in session.events()
    )


def test_candidate_tracker_ranks_repeated_destination_first() -> None:
    tracker = CandidateTracker()
    for destination in (
        ("203.0.113.20", 9000),
        ("203.0.113.10", 443),
        ("203.0.113.20", 9000),
    ):
        tracker.consume(
            NetworkObservation(
                "connection_attempt",
                {
                    "destination_ip": destination[0],
                    "destination_port": destination[1],
                },
            )
        )

    candidates = tracker.candidates()

    assert [(item.destination_ip, item.destination_port) for item in candidates] == [
        ("203.0.113.20", 9000),
        ("203.0.113.10", 443),
    ]


def test_tshark_parser_retains_ethernet_and_ip_destination() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "aa:bb:cc:dd:ee:ff",
                "ip.src": "192.0.2.20",
                "ip.dst": "198.51.100.40",
                "tcp.srcport": "51000",
                "tcp.dstport": "9000",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "connection_attempt"
    assert observation.metadata["destination_ip"] == "198.51.100.40"
    assert observation.metadata["destination_port"] == 9000
    assert observation.metadata["destination_mac"] == "aa:bb:cc:dd:ee:ff"


def test_tshark_parser_retains_arp_resolution() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "ff:ff:ff:ff:ff:ff",
                "arp.opcode": "1",
                "arp.src.proto_ipv4": "192.0.2.20",
                "arp.dst.proto_ipv4": "198.51.100.40",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "arp_request"
    assert observation.metadata["target_ip"] == "198.51.100.40"


def test_tshark_preflight_has_friendly_missing_dependency_error() -> None:
    observer = TsharkObserver(executable=None)
    observer.executable = None

    with pytest.raises(DiscoveryPreflightError, match="TShark/Wireshark CLI"):
        observer.preflight("eth0")


def test_tshark_parser_retains_dns_query_identity() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "aa:bb:cc:dd:ee:ff",
                "ip.src": "192.0.2.20",
                "ip.dst": "192.0.2.53",
                "dns.id": "4242",
                "dns.flags.response": "0",
                "dns.qry.type": "1",
                "dns.qry.name": "csms.example.com",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "dns_query"
    assert observation.metadata["dns_id"] == 4242
    assert observation.metadata["record_type"] == "A"
    assert observation.metadata["name"] == "csms.example.com"
    assert observation.metadata["client_ip"] == "192.0.2.20"
    assert observation.metadata["dns_server"] == "192.0.2.53"


def test_tshark_parser_retains_dns_a_response() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "aa:bb:cc:dd:ee:ff",
                "eth.dst": "00:11:22:33:44:55",
                "ip.src": "192.0.2.53",
                "ip.dst": "192.0.2.20",
                "dns.id": "4242",
                "dns.flags.response": "1",
                "dns.qry.type": "1",
                "dns.qry.name": "csms.example.com",
                "dns.a": "198.51.100.40",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "dns_response"
    assert observation.metadata["dns_id"] == 4242
    assert observation.metadata["address"] == "198.51.100.40"
    assert observation.metadata["client_ip"] == "192.0.2.20"
    assert observation.metadata["dns_server"] == "192.0.2.53"


def test_dns_resolution_is_evidence_and_enriches_later_candidate(tmp_path) -> None:
    observer = FakeObserver(
        [
            NetworkObservation(
                "dns_query",
                {
                    "dns_id": 4242,
                    "record_type": "A",
                    "name": "csms.example.com",
                    "client_ip": "192.0.2.20",
                    "dns_server": "192.0.2.53",
                },
            ),
            NetworkObservation(
                "dns_response",
                {
                    "dns_id": 4242,
                    "address": "198.51.100.40",
                    "client_ip": "192.0.2.20",
                    "dns_server": "192.0.2.53",
                },
            ),
            NetworkObservation(
                "connection_attempt",
                {
                    "source_ip": "192.0.2.20",
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                },
            ),
        ]
    )

    session = run_passive_discovery(
        interface="eth0",
        role="control",
        root=tmp_path,
        observer=observer,
    )

    events = session.events()
    resolution = next(
        event for event in events if event["event_type"] == "dns_resolution"
    )
    assert resolution["category"] == "observation"
    assert resolution["metadata"]["name"] == "csms.example.com"
    assert resolution["metadata"]["address"] == "198.51.100.40"

    candidate = next(
        event for event in events if event["event_type"] == "csms_candidate"
    )
    assert candidate["metadata"]["hostnames"] == ["csms.example.com"]


def test_unmatched_dns_response_does_not_invent_hostname() -> None:
    tracker = CandidateTracker()
    derived = tracker.consume(
        NetworkObservation(
            "dns_response",
            {
                "dns_id": 4242,
                "address": "198.51.100.40",
                "client_ip": "192.0.2.20",
                "dns_server": "192.0.2.53",
            },
        )
    )
    tracker.consume(
        NetworkObservation(
            "connection_attempt",
            {
                "destination_ip": "198.51.100.40",
                "destination_port": 9000,
            },
        )
    )

    assert derived == []
    assert tracker.candidates()[0].hostnames == set()


def test_tshark_parser_retains_plain_http_request_metadata() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "aa:bb:cc:dd:ee:ff",
                "ip.src": "192.0.2.20",
                "ip.dst": "198.51.100.40",
                "tcp.srcport": "51000",
                "tcp.dstport": "9000",
                "http.request.method": "GET",
                "http.host": "csms.example.com:9000",
                "http.request.uri": "/health",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "http_request"
    assert observation.metadata["method"] == "GET"
    assert observation.metadata["hostname"] == "csms.example.com:9000"
    assert observation.metadata["path"] == "/health"
    assert "subprotocol" not in observation.metadata


def test_tshark_parser_retains_websocket_upgrade_metadata() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "aa:bb:cc:dd:ee:ff",
                "ip.src": "192.0.2.20",
                "ip.dst": "198.51.100.40",
                "tcp.srcport": "51000",
                "tcp.dstport": "9000",
                "http.request.method": "GET",
                "http.host": "csms.example.com:9000",
                "http.request.uri": "/ocpp/CP001",
                "http.upgrade": "websocket",
                "http.sec_websocket_protocol": "ocpp1.6",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "websocket_upgrade"
    assert observation.metadata["hostname"] == "csms.example.com:9000"
    assert observation.metadata["path"] == "/ocpp/CP001"
    assert observation.metadata["subprotocol"] == "ocpp1.6"


def test_websocket_evidence_enriches_candidate_even_without_fresh_arp() -> None:
    tracker = CandidateTracker()

    derived = tracker.consume(
        NetworkObservation(
            "websocket_upgrade",
            {
                "destination_ip": "198.51.100.40",
                "destination_port": 9000,
                "destination_mac": "aa:bb:cc:dd:ee:ff",
                "hostname": "csms.example.com:9000",
                "path": "/ocpp/CP001",
                "subprotocol": "ocpp1.6",
            },
        )
    )

    candidate = tracker.candidates()[0]
    assert candidate.hostnames == {"csms.example.com:9000"}
    assert candidate.http_paths == {"/ocpp/CP001"}
    assert candidate.websocket_paths == {"/ocpp/CP001"}
    assert candidate.ocpp_subprotocols == {"ocpp1.6"}
    assert candidate.mac_without_resolution is True
    assert [item.event_type for item in derived] == [
        "destination_mac_observed_without_resolution"
    ]


def test_http_request_does_not_invent_websocket_metadata() -> None:
    tracker = CandidateTracker()
    tracker.consume(
        NetworkObservation(
            "http_request",
            {
                "destination_ip": "198.51.100.40",
                "destination_port": 9000,
                "hostname": "csms.example.com",
                "path": "/health",
            },
        )
    )

    candidate = tracker.candidates()[0]
    assert candidate.hostnames == {"csms.example.com"}
    assert candidate.http_paths == {"/health"}
    assert candidate.websocket_paths == set()
    assert candidate.ocpp_subprotocols == set()


def test_tshark_parser_retains_tls_client_hello_sni() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "eth.src": "00:11:22:33:44:55",
                "eth.dst": "aa:bb:cc:dd:ee:ff",
                "ip.src": "192.0.2.20",
                "ip.dst": "198.51.100.40",
                "tcp.srcport": "51000",
                "tcp.dstport": "443",
                "tls.handshake.type": "1",
                "tls.handshake.extensions_server_name": "secure.example.com",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "tls_client_hello"
    assert observation.metadata["destination_ip"] == "198.51.100.40"
    assert observation.metadata["destination_port"] == 443
    assert observation.metadata["sni"] == "secure.example.com"


def test_tls_client_hello_without_sni_is_still_observed() -> None:
    observation = parse_tshark_line(
        tshark_row(
            **{
                "ip.src": "192.0.2.20",
                "ip.dst": "198.51.100.40",
                "tcp.srcport": "51000",
                "tcp.dstport": "443",
                "tls.handshake.type": "1",
            }
        )
    )

    assert observation is not None
    assert observation.event_type == "tls_client_hello"
    assert observation.metadata["sni"] is None


def test_tls_sni_enriches_candidate_without_becoming_http_hostname() -> None:
    tracker = CandidateTracker()

    tracker.consume(
        NetworkObservation(
            "tls_client_hello",
            {
                "destination_ip": "198.51.100.40",
                "destination_port": 443,
                "sni": "secure.example.com",
            },
        )
    )

    candidate = tracker.candidates()[0]
    assert candidate.tls_sni == {"secure.example.com"}
    assert candidate.hostnames == set()
    assert candidate.http_paths == set()
    assert candidate.websocket_paths == set()
