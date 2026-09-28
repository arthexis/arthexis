import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.ocpp.models import OcppPolicy
from apps.ocpp.services import readiness
from apps.ocpp.services.readiness import query_ocpp_readiness

pytestmark = pytest.mark.django_db


@override_settings(
    OCPP_TRUSTED_CHARGER_INTERFACE="eth0",
    OCPP_MAX_CONNECTIONS=64,
    OCPP_WEBSOCKET_CONNECT_TIMEOUT_SECONDS=7,
    OCPP_WEBSOCKET_PING_INTERVAL_SECONDS=11,
    OCPP_WEBSOCKET_PING_TIMEOUT_SECONDS=13,
    EVENT_DISPATCH_BATCH_SIZE=23,
)
def test_readiness_reports_effective_trusted_listener_and_bounds(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"} if interface == "eth0" else set(),
    )
    policy = OcppPolicy.load()
    policy.charger_admission_mode = OcppPolicy.AdmissionMode.RESTRICTED
    policy.save()

    payload = query_ocpp_readiness()

    assert payload == {
        "trusted_interface": "eth0",
        "trusted_interface_addresses": ["192.0.2.10"],
        "trusted_listener_status": "ready",
        "trusted_listener_admission": "open",
        "instance_charger_admission": "restricted",
        "max_connections": 64,
        "websocket_connect_timeout_seconds": 7,
        "websocket_ping_interval_seconds": 11,
        "websocket_ping_timeout_seconds": 13,
        "event_dispatch_batch_size": 23,
        "network_boundary_owner": "gway",
    }


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="eth0")
def test_readiness_marks_configured_interface_without_address_unavailable(
    monkeypatch,
) -> None:
    monkeypatch.setattr(readiness, "interface_addresses", lambda interface: set())

    payload = query_ocpp_readiness()

    assert payload["trusted_listener_status"] == "unavailable"
    assert payload["trusted_listener_admission"] is None


@override_settings(OCPP_TRUSTED_CHARGER_INTERFACE="")
def test_readiness_marks_trusted_listener_disabled() -> None:
    payload = query_ocpp_readiness()

    assert payload["trusted_interface"] is None
    assert payload["trusted_listener_status"] == "disabled"
    assert payload["trusted_listener_admission"] is None


def test_ocpp_readiness_json_is_machine_readable(monkeypatch) -> None:
    monkeypatch.setattr(
        readiness,
        "interface_addresses",
        lambda interface: {"192.0.2.10"},
    )
    stdout = StringIO()

    call_command("ocpp_readiness", json_output=True, stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert payload["trusted_listener_status"] == "ready"
    assert payload["network_boundary_owner"] == "gway"
