from apps.ocpp.management.commands.ocpp_simulator import Command


def test_profile_overrides_only_include_explicit_cli_values():
    options = {
        "charger": "GW001-CLI",
        "protocol": None,
        "connectors": None,
        "timeout": None,
        "allow_insecure_ws": False,
        "vendor": None,
        "model": "CLI Model",
        "serial": None,
        "firmware_version": None,
        "authorization_timeout": 300.0,
        "heartbeat": None,
        "reconnect": False,
        "clock_mode": "offset",
        "clock_offset_seconds": -120.0,
        "clock_start_time": None,
    }

    assert Command._profile_overrides(options, "wss://override.test/ws") == {
        "identity": "GW001-CLI",
        "target": {"url": "wss://override.test/ws"},
        "boot": {"model": "CLI Model"},
        "behavior": {
            "authorization_timeout_seconds": 300.0,
            "reconnect": False,
        },
        "clock": {"mode": "offset", "offset_seconds": -120.0},
    }


def test_profile_overrides_include_explicit_connector_count():
    assert Command._profile_overrides({"connectors": 2}, None) == {"connectors": 2}
