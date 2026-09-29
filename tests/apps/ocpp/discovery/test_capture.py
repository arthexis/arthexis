import json

from apps.ocpp.discovery.capture import (
    CapturePlan,
    CaptureRequest,
    GwayCaptureProvider,
    active_redirect,
    capture_request_from_candidate,
    release_capture,
    start_capture,
)
from apps.ocpp.discovery.session import DiscoverySession


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



class Completed:
    def __init__(self, payload, returncode=0, stderr=""):
        self.returncode = returncode
        self.stdout = json.dumps(payload)
        self.stderr = stderr


def test_gway_provider_uses_json_cli_for_apply_status_and_release() -> None:
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["network", "available"]:
            return Completed(
                {
                    "available": True,
                    "backend": "nftables",
                    "strategy": "destination-redirect",
                }
            )
        if command[1:3] == ["network", "capture"]:
            return Completed({"id": "abc123def456", "active": True})
        if command[1:3] == ["network", "status"]:
            return Completed({"id": "abc123def456", "active": True})
        if command[1:3] == ["network", "remove"]:
            return Completed(
                {"id": "abc123def456", "active": False, "changed": True}
            )
        raise AssertionError(command)

    provider = GwayCaptureProvider("/usr/bin/gway", runner=runner)
    request = CaptureRequest(
        interface="eth0",
        destination_ip="198.51.100.40",
        destination_port=9000,
        local_host="127.0.0.1",
        local_port=9000,
    )
    plan = provider.plan(request)
    applied = provider.apply(plan)
    status = provider.status(applied["id"])
    released = provider.release(applied["id"])

    assert applied["id"] == "abc123def456"
    assert status["active"] is True
    assert released["changed"] is True
    redirect = calls[1]
    assert redirect[:4] == [
        "/usr/bin/gway",
        "network",
        "capture",
        "eth0",
    ]
    assert "--sudo" in redirect
    assert all(command[-1] == "--json" for command in calls)


def test_capture_lifecycle_persists_handle_and_releases_after_reopen(tmp_path) -> None:
    class Provider:
        name = "gway"

        def __init__(self):
            self.released = []

        def plan(self, request):
            return CapturePlan(
                provider=self.name,
                strategy="destination-redirect",
                request=request,
            )

        def apply(self, plan):
            return {"id": "abc123def456", "active": True}

        def status(self, redirect_id):
            return {"id": redirect_id, "active": True}

        def release(self, redirect_id):
            self.released.append(redirect_id)
            return {"id": redirect_id, "active": False, "changed": True}

    session = DiscoverySession.create(tmp_path, session_id="manual-capture")
    session.record(
        "csms_candidate",
        category="inference",
        metadata={
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "source_ips": ["192.0.2.20"],
            "hostnames": [],
        },
    )
    provider = Provider()

    started = start_capture(
        session,
        interface="eth0",
        provider=provider,
    )

    assert started["active"] is True
    assert started["redirect_id"] == "abc123def456"
    reopened = DiscoverySession.open(tmp_path, "manual-capture")
    assert active_redirect(reopened.events())["redirect_id"] == "abc123def456"

    released = release_capture(reopened, provider=provider)

    assert released == {
        "active": False,
        "changed": True,
        "redirect_id": "abc123def456",
        "handoff_disarmed": True,
        "handoff_claimed": False,
    }
    assert provider.released == ["abc123def456"]
    assert active_redirect(reopened.events()) is None


def test_release_failure_keeps_redirect_owned_for_retry(tmp_path) -> None:
    class Provider:
        name = "gway"

        def release(self, redirect_id):
            raise RuntimeError("nft unavailable")

    session = DiscoverySession.create(tmp_path, session_id="release-retry")
    session.record(
        "capture_started",
        metadata={
            "provider": "gway",
            "strategy": "destination-redirect",
            "redirect_id": "abc123def456",
        },
    )

    result = release_capture(session, provider=Provider())

    assert result["active"] is True
    assert result["reason"] == "redirect_remove_failed"
    assert active_redirect(session.events())["redirect_id"] == "abc123def456"
    assert session.write_summary()["capture"]["release_failure_reason"] == (
        "redirect_remove_failed"
    )



def test_capture_arms_handoff_before_redirect_apply(tmp_path) -> None:
    observed = []

    class Provider:
        name = "gway"

        def plan(self, request):
            return CapturePlan(
                provider=self.name,
                strategy="destination-redirect",
                request=request,
            )

        def apply(self, plan):
            observed.append(plan.request.destination_ip)
            assert session.handoff_path.exists()
            return {"id": "abc123def456", "active": True}

        def status(self, redirect_id):
            return {"id": redirect_id, "active": True}

        def release(self, redirect_id):
            return {"id": redirect_id, "active": False, "changed": True}

    session = DiscoverySession.create(tmp_path, session_id="arm-before-apply")
    session.record(
        "csms_candidate",
        category="inference",
        metadata={
            "source_ips": ["192.0.2.20"],
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "hostnames": ["csms.example.com"],
        },
    )

    result = start_capture(session, interface="eth0", provider=Provider())

    assert result["active"] is True
    assert observed == ["198.51.100.40"]
    assert session.write_summary()["handoff_armed"] is True


def test_redirect_apply_failure_disarms_handoff(tmp_path) -> None:
    class Provider:
        name = "gway"

        def plan(self, request):
            return CapturePlan(
                provider=self.name,
                strategy="destination-redirect",
                request=request,
            )

        def apply(self, plan):
            raise RuntimeError("apply failed")

    session = DiscoverySession.create(tmp_path, session_id="apply-failure")
    session.record(
        "csms_candidate",
        category="inference",
        metadata={
            "source_ips": ["192.0.2.20"],
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "hostnames": [],
        },
    )

    result = start_capture(session, interface="eth0", provider=Provider())

    assert result == {"active": False, "reason": "redirect_apply_failed"}
    assert not session.handoff_path.exists()
    assert any(
        event["event_type"] == "handoff_disarmed"
        for event in session.events()
    )


def test_capture_requires_unambiguous_source_host_for_handoff(tmp_path) -> None:
    class Provider:
        name = "gway"

        def plan(self, request):
            return CapturePlan(
                provider=self.name,
                strategy="destination-redirect",
                request=request,
            )

        def apply(self, plan):
            raise AssertionError("redirect must not be applied")

    session = DiscoverySession.create(tmp_path, session_id="ambiguous-source")
    session.record(
        "csms_candidate",
        category="inference",
        metadata={
            "source_ips": ["192.0.2.20", "192.0.2.21"],
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "hostnames": [],
        },
    )

    result = start_capture(session, interface="eth0", provider=Provider())

    assert result == {"active": False, "reason": "handoff_match_unavailable"}
    assert not session.handoff_path.exists()


def test_release_after_claim_keeps_claim_evidence(tmp_path) -> None:
    class Provider:
        name = "gway"

        def release(self, redirect_id):
            return {"id": redirect_id, "active": False, "changed": True}

    session = DiscoverySession.create(tmp_path, session_id="release-claimed")
    session.record(
        "capture_started",
        metadata={
            "provider": "gway",
            "strategy": "destination-redirect",
            "redirect_id": "abc123def456",
        },
    )
    session.arm_handoff(client_host="192.0.2.20")
    assert session.claim_handoff(
        charger_identity="CP001",
        client_host="192.0.2.20",
    ) is not None

    result = release_capture(session, provider=Provider())

    assert result["active"] is False
    assert result["handoff_disarmed"] is False
    assert result["handoff_claimed"] is True
    assert session.claimed_handoff_path.exists()
