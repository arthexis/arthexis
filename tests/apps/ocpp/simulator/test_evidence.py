import json

from apps.ocpp.simulator.evidence import create_session_evidence


def test_session_evidence_retains_source_and_effective_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("OCPP_SIMULATOR_RUNTIME_DIR", str(tmp_path))
    source = {
        "identity": "PROFILE-ID",
        "target": {"url": "wss://profile.test/ws"},
    }
    effective = {
        "identity": "CLI-ID",
        "target": {"url": "wss://override.test/ws"},
    }

    path = create_session_evidence(
        "CLI-ID",
        source_profile=source,
        effective_profile=effective,
    )

    assert json.loads((path / "profile.json").read_text()) == source
    assert json.loads((path / "effective-profile.json").read_text()) == effective
    assert path.parent == tmp_path / "sessions"
