from pathlib import Path

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
