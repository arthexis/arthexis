from pathlib import Path

from apps.ocpp.discovery.capture import CapturePlan
from apps.ocpp.discovery.session import DiscoverySession
from apps.ocpp.management.charger import discover as discover_module


def test_discover_uses_configured_data_root(monkeypatch, settings, tmp_path) -> None:
    captured = {}

    class FakeObserver:
        pass

    class FakeSession:
        session_id = "test-session"

        def write_summary(self):
            return {"session_id": self.session_id}

        def events(self):
            return [
                {
                    "event_type": "interface_selected",
                    "metadata": {"interface": "eth0", "role": "control"},
                }
            ]

    def fake_run(**kwargs):
        captured.update(kwargs)
        return FakeSession()

    settings.DATA_DIR = tmp_path
    monkeypatch.setattr(discover_module, "TsharkObserver", FakeObserver)
    monkeypatch.setattr(discover_module, "run_passive_discovery", fake_run)

    result = discover_module.discover(interface="eth0", role="control")

    assert result["session_id"] == "test-session"
    assert result["display"] == (
        "Discovery session test-session\n"
        "Interface: eth0 (control)\n"
        "No CSMS candidates observed."
    )
    assert captured["interface"] == "eth0"
    assert captured["role"] == "control"
    assert captured["root"] == Path(tmp_path)
    assert isinstance(captured["observer"], FakeObserver)



def test_capture_request_without_gway_keeps_discovery_report(
    monkeypatch,
    settings,
    tmp_path,
) -> None:
    settings.DATA_DIR = tmp_path

    def fake_run(**kwargs):
        session = DiscoverySession.create(tmp_path, session_id="capture-no-gway")
        session.record(
            "csms_candidate",
            category="inference",
            metadata={
                "destination_ip": "198.51.100.40",
                "destination_port": 9000,
                "destination_mac": "aa:bb:cc:dd:ee:ff",
                "source_ips": ["192.0.2.20"],
                "hostnames": ["csms.example.com"],
            },
        )
        return session

    monkeypatch.setattr(discover_module, "run_passive_discovery", fake_run)
    monkeypatch.setattr(
        "apps.ocpp.discovery.capture.default_capture_provider",
        lambda: None,
    )

    result = discover_module.discover(
        interface="eth0",
        role="control",
        capture=True,
    )

    assert result["capture"] == {
        "requested": True,
        "available": False,
        "attempted": False,
        "succeeded": False,
        "strategy": None,
        "failure_reason": "capture_provider_unavailable",
        "redirect_id": None,
        "active": False,
        "release_failure_reason": None,
    }
    assert "Capture: unavailable (capture_provider_unavailable)" in result["display"]


def test_capture_request_with_provider_records_plan_without_mutation(
    monkeypatch,
    settings,
    tmp_path,
) -> None:
    settings.DATA_DIR = tmp_path

    def fake_run(**kwargs):
        session = DiscoverySession.create(tmp_path, session_id="capture-provider")
        session.record(
            "csms_candidate",
            category="inference",
            metadata={
                "destination_ip": "198.51.100.40",
                "destination_port": 9000,
                "destination_mac": "aa:bb:cc:dd:ee:ff",
                "source_ips": ["192.0.2.20"],
                "hostnames": ["csms.example.com"],
            },
        )
        return session

    class FakeProvider:
        name = "fake-gway"

        def __init__(self) -> None:
            self.requests = []

        def plan(self, request):
            self.requests.append(request)
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
            return {"id": redirect_id, "active": False, "changed": True}

    provider = FakeProvider()
    monkeypatch.setattr(discover_module, "run_passive_discovery", fake_run)
    monkeypatch.setattr(
        "apps.ocpp.discovery.capture.default_capture_provider",
        lambda: provider,
    )

    result = discover_module.discover(
        interface="eth0",
        role="control",
        capture=True,
    )

    assert len(provider.requests) == 1
    assert provider.requests[0].destination_ip == "198.51.100.40"
    assert result["capture"] == {
        "requested": True,
        "available": True,
        "attempted": True,
        "succeeded": False,
        "strategy": "destination-redirect",
        "failure_reason": None,
        "redirect_id": "abc123def456",
        "active": True,
        "release_failure_reason": None,
    }
    assert (
        "Capture: available via fake-gway (destination-redirect)"
        in result["display"]
    )


def test_capture_request_without_candidate_reports_unavailable(
    monkeypatch,
    settings,
    tmp_path,
) -> None:
    settings.DATA_DIR = tmp_path

    def fake_run(**kwargs):
        return DiscoverySession.create(tmp_path, session_id="capture-no-candidate")

    monkeypatch.setattr(discover_module, "run_passive_discovery", fake_run)

    result = discover_module.discover(
        interface="eth0",
        role="control",
        capture=True,
    )

    assert result["capture"]["requested"] is True
    assert result["capture"]["available"] is False
    assert result["capture"]["failure_reason"] == "no_csms_candidate"
