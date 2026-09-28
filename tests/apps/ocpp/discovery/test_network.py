import pytest

from apps.ocpp.discovery.network import (
    CandidateTracker,
    DiscoveryPreflightError,
    NetworkObservation,
    TcpdumpObserver,
    parse_tcpdump_line,
    run_passive_discovery,
)


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


def test_tcpdump_parser_retains_ethernet_and_ip_destination() -> None:
    observation = parse_tcpdump_line(
        "1790618400.000000 00:11:22:33:44:55 > aa:bb:cc:dd:ee:ff, "
        "ethertype IPv4 (0x0800), length 74: "
        "192.0.2.20.51000 > 198.51.100.40.9000: Flags [S]"
    )

    assert observation is not None
    assert observation.event_type == "connection_attempt"
    assert observation.metadata["destination_ip"] == "198.51.100.40"
    assert observation.metadata["destination_port"] == 9000
    assert observation.metadata["destination_mac"] == "aa:bb:cc:dd:ee:ff"


def test_tcpdump_parser_retains_arp_resolution() -> None:
    observation = parse_tcpdump_line(
        "1790618400.000000 00:11:22:33:44:55 > ff:ff:ff:ff:ff:ff, "
        "ethertype ARP (0x0806), length 42: "
        "Request who-has 198.51.100.40 tell 192.0.2.20"
    )

    assert observation is not None
    assert observation.event_type == "arp_request"
    assert observation.metadata["target_ip"] == "198.51.100.40"


def test_tcpdump_preflight_has_friendly_missing_dependency_error() -> None:
    observer = TcpdumpObserver(executable=None)
    observer.executable = None

    with pytest.raises(DiscoveryPreflightError, match="native packet-capture"):
        observer.preflight("eth0")
