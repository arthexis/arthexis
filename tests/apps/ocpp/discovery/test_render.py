from apps.ocpp.discovery.render import render_discovery_events


def test_render_discovery_events_uses_structured_evidence() -> None:
    output = render_discovery_events(
        "field-001",
        [
            {
                "event_type": "interface_selected",
                "metadata": {"interface": "eth0", "role": "satellite"},
            },
            {
                "event_type": "dns_resolution",
                "metadata": {
                    "name": "csms.example.com",
                    "address": "198.51.100.40",
                },
            },
            {
                "event_type": "destination_mac_observed_without_resolution",
                "metadata": {
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "destination_mac": "aa:bb:cc:dd:ee:ff",
                },
            },
            {
                "event_type": "csms_candidate",
                "metadata": {
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "destination_mac": "aa:bb:cc:dd:ee:ff",
                    "hostnames": ["csms.example.com"],
                    "http_paths": ["/ocpp/CP001"],
                    "websocket_paths": ["/ocpp/CP001"],
                    "ocpp_subprotocols": ["ocpp1.6"],
                    "tls_sni": ["secure.example.com"],
                    "attempts": 3,
                },
            },
        ],
    )

    assert output.splitlines() == [
        "Discovery session field-001",
        "Interface: eth0 (satellite)",
        "DNS: csms.example.com -> 198.51.100.40",
        (
            "Layer 2: 198.51.100.40:9000 via aa:bb:cc:dd:ee:ff "
            "(no fresh ARP observed)"
        ),
        "CSMS candidates:",
        (
            "  1. 198.51.100.40:9000 attempts=3 "
            "(host=csms.example.com; http=/ocpp/CP001; ws=/ocpp/CP001; "
            "protocol=ocpp1.6; sni=secure.example.com; "
            "mac=aa:bb:cc:dd:ee:ff)"
        ),
    ]


def test_render_discovery_events_handles_no_candidate() -> None:
    output = render_discovery_events(
        "field-002",
        [
            {
                "event_type": "interface_selected",
                "metadata": {"interface": "eth1", "role": "control"},
            }
        ],
    )

    assert output == (
        "Discovery session field-002\n"
        "Interface: eth1 (control)\n"
        "No CSMS candidates observed."
    )



def test_render_discovery_events_includes_link_dhcp_and_retry_cadence() -> None:
    output = render_discovery_events(
        "field-003",
        [
            {
                "event_type": "interface_selected",
                "metadata": {"interface": "eth0", "role": "control"},
            },
            {
                "event_type": "link_state",
                "metadata": {"interface": "eth0", "state": "up"},
            },
            {
                "event_type": "dhcp",
                "metadata": {
                    "message_type": 3,
                    "requested_address": "192.0.2.20",
                    "offered_address": None,
                },
            },
            {
                "event_type": "csms_candidate",
                "metadata": {
                    "destination_ip": "198.51.100.40",
                    "destination_port": 9000,
                    "hostnames": [],
                    "http_paths": [],
                    "websocket_paths": [],
                    "ocpp_subprotocols": [],
                    "tls_sni": [],
                    "attempts": 3,
                    "retry_intervals_seconds": [2.5, 4.5],
                },
            },
        ],
    )

    assert output.splitlines() == [
        "Discovery session field-003",
        "Interface: eth0 (control)",
        "Link: eth0 up",
        "DHCP: type=3 (requested=192.0.2.20)",
        "CSMS candidates:",
        "  1. 198.51.100.40:9000 attempts=3 (retry=2.5s,4.5s)",
    ]
