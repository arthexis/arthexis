from apps.ocpp.discovery.capture import CapturePlan
from apps.ocpp.discovery.session import DiscoverySession
from apps.ocpp.management.charger import capture as capture_module
from apps.ocpp.management.charger import release as release_module


def _session(tmp_path):
    session = DiscoverySession.create(tmp_path, session_id="manual-session")
    session.record(
        "interface_selected",
        metadata={"interface": "eth0", "role": "control"},
    )
    session.record(
        "csms_candidate",
        category="inference",
        metadata={
            "destination_ip": "198.51.100.40",
            "destination_port": 9000,
            "hostnames": [],
        },
    )
    return session


def test_manual_capture_uses_persisted_interface(
    monkeypatch,
    settings,
    tmp_path,
) -> None:
    settings.DATA_DIR = tmp_path
    _session(tmp_path)

    class Provider:
        name = "gway"

        def plan(self, request):
            assert request.interface == "eth0"
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

    monkeypatch.setattr(
        "apps.ocpp.discovery.capture.default_capture_provider",
        lambda: Provider(),
    )

    result = capture_module.capture("manual-session")

    assert result["capture"]["active"] is True
    assert result["capture"]["redirect_id"] == "abc123def456"
    assert result["capture_action"]["active"] is True


def test_manual_release_recovers_persisted_handle_after_reopen(
    monkeypatch,
    settings,
    tmp_path,
) -> None:
    settings.DATA_DIR = tmp_path
    session = _session(tmp_path)
    session.record(
        "capture_started",
        metadata={
            "provider": "gway",
            "strategy": "destination-redirect",
            "redirect_id": "abc123def456",
        },
    )
    released = []

    class Provider:
        name = "gway"

        def release(self, redirect_id):
            released.append(redirect_id)
            return {"id": redirect_id, "active": False, "changed": True}

    monkeypatch.setattr(
        "apps.ocpp.discovery.capture.default_capture_provider",
        lambda: Provider(),
    )

    result = release_module.release("manual-session")

    assert released == ["abc123def456"]
    assert result["capture"]["active"] is False
    assert result["capture_action"]["changed"] is True
