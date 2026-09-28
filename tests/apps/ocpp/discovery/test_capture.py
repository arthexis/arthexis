from apps.ocpp.discovery.capture import (
    CapturePlan,
    CaptureRequest,
    capture_request_from_candidate,
)


def test_capture_request_from_candidate_preserves_semantic_destination() -> None:
    request = capture_request_from_candidate(
        interface="eth0",
        candidate={
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "destination_mac": "aa:bb:cc:dd:ee:ff",
            "hostnames": ["csms.example.com"],
        },
        local_host="127.0.0.1",
        local_port=9000,
    )

    assert request == CaptureRequest(
        interface="eth0",
        destination_ip="198.51.100.40",
        destination_port=9000,
        destination_mac="aa:bb:cc:dd:ee:ff",
        hostname="csms.example.com",
        local_host="127.0.0.1",
        local_port=9000,
    )


def test_capture_contract_is_strategy_neutral() -> None:
    request = CaptureRequest(
        interface="eth0",
        destination_ip="2001:db8::40",
        destination_port=443,
        local_host="::1",
        local_port=9000,
    )
    plan = CapturePlan(
        provider="fake-gway",
        strategy="destination-nat",
        request=request,
    )

    assert plan.request.destination_ip == "2001:db8::40"
    assert plan.strategy == "destination-nat"
