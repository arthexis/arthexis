import json

import pytest

from apps.ocpp.simulator.profile import (
    ChargerProfile,
    JsonChargerProfileSource,
    load_profile,
)


def test_json_profile_loads_and_normalizes(tmp_path):
    path = tmp_path / "charger.json"
    path.write_text(
        json.dumps(
            {
                "identity": "GW001-SIM",
                "connectors": 2,
                "target": {"url": "wss://example.test/ws", "timeout_seconds": 45},
                "boot": {"vendor": "Vendor X", "model": "Model Y"},
                "behavior": {"authorization_timeout_seconds": 300},
                "clock": {"mode": "offset", "offset_seconds": -90},
            }
        )
    )

    profile = load_profile(JsonChargerProfileSource(path))

    assert profile.identity == "GW001-SIM"
    assert profile.protocol == "ocpp1.6j"
    assert profile.connectors == 2
    assert profile.target["url"] == "wss://example.test/ws"
    assert profile.target["timeout_seconds"] == 45.0
    assert profile.behavior["authorization_timeout_seconds"] == 300.0
    assert profile.clock == {
        "mode": "offset",
        "offset_seconds": -90.0,
        "start_time": None,
    }
    assert profile.configuration == {}


def test_profile_defaults_to_one_connector():
    profile = ChargerProfile.from_mapping({"identity": "SIM"})
    assert profile.connectors == 1
    assert profile.as_dict()["connectors"] == 1


def test_profile_rejects_nonpositive_connector_count():
    with pytest.raises(ValueError, match="connectors"):
        ChargerProfile.from_mapping({"identity": "SIM", "connectors": 0})


def test_cli_style_overrides_win_without_erasing_nested_profile_values(tmp_path):
    path = tmp_path / "charger.json"
    path.write_text(
        json.dumps(
            {
                "identity": "PROFILE-ID",
                "connectors": 1,
                "target": {"url": "wss://profile.test/ws", "timeout_seconds": 50},
                "boot": {"vendor": "Profile Vendor", "model": "Profile Model"},
            }
        )
    )

    profile = load_profile(
        JsonChargerProfileSource(path),
        overrides={
            "identity": "CLI-ID",
            "connectors": 2,
            "target": {"url": "wss://override.test/ws"},
            "boot": {"model": "CLI Model"},
        },
    )

    assert profile.identity == "CLI-ID"
    assert profile.connectors == 2
    assert profile.target == {
        "url": "wss://override.test/ws",
        "allow_insecure_ws": False,
        "timeout_seconds": 50.0,
    }
    assert profile.boot["vendor"] == "Profile Vendor"
    assert profile.boot["model"] == "CLI Model"


def test_profile_rejects_missing_identity():
    with pytest.raises(ValueError, match="requires identity"):
        ChargerProfile.from_mapping({})


def test_profile_requires_clock_start_for_advancing_mode():
    with pytest.raises(ValueError, match="start_time"):
        ChargerProfile.from_mapping(
            {
                "identity": "SIM",
                "clock": {"mode": "advancing"},
            }
        )
