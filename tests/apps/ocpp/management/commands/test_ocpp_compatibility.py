import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.ocpp.services.compatibility import record_compatibility_evidence
from apps.ocpp.services.compatibility_diagnostics import (
    query_compatibility_diagnostics,
)
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_diagnostics_filters_counts_and_orders_recent_evidence() -> None:
    selected = charger("field-charger")
    other = charger("other-charger")
    record_compatibility_evidence(
        kind="protocol_fallback",
        charger=selected,
        protocol="ocpp1.6",
        details={"offered_subprotocols": ["vendor-ocpp"]},
    )
    record_compatibility_evidence(
        kind="unsupported_action",
        charger=selected,
        protocol="ocpp1.6",
        unique_id="vendor-1",
        action="VendorMagic",
        details={"payload": {"x": 1}},
    )
    record_compatibility_evidence(
        kind="unsupported_action",
        charger=other,
        protocol="ocpp1.6",
        action="OtherMagic",
    )

    payload = query_compatibility_diagnostics(
        charger_identity=selected.identity,
        limit=10,
    )

    assert payload["total"] == 2
    assert payload["counts"] == {
        "protocol_fallback": 1,
        "unsupported_action": 1,
    }
    assert [event["kind"] for event in payload["events"]] == [
        "unsupported_action",
        "protocol_fallback",
    ]
    assert payload["events"][0]["action"] == "VendorMagic"
    assert payload["events"][0]["unique_id"] == "vendor-1"


def test_diagnostics_kind_filter_does_not_mix_other_evidence() -> None:
    selected = charger("kind-filter")
    record_compatibility_evidence(kind="malformed_frame", charger=selected)
    record_compatibility_evidence(kind="unmatched_response", charger=selected)

    payload = query_compatibility_diagnostics(
        charger_identity=selected.identity,
        kinds=("malformed_frame",),
        limit=10,
    )

    assert payload["total"] == 1
    assert payload["counts"] == {"malformed_frame": 1}
    assert [event["kind"] for event in payload["events"]] == [
        "malformed_frame"
    ]


def test_ocpp_compatibility_json_is_machine_readable() -> None:
    selected = charger("compat-json")
    record_compatibility_evidence(
        kind="protocol_fallback",
        charger=selected,
        protocol="ocpp1.6",
        details={"offered_subprotocols": []},
    )
    stdout = StringIO()

    call_command(
        "ocpp_compatibility",
        charger=selected.identity,
        json_output=True,
        stdout=stdout,
    )

    payload = json.loads(stdout.getvalue())
    assert payload["charger"] == selected.identity
    assert payload["total"] == 1
    assert payload["counts"] == {"protocol_fallback": 1}
    assert payload["events"][0]["protocol"] == "ocpp1.6"


def test_ocpp_compatibility_text_surfaces_field_relevant_context() -> None:
    selected = charger("compat-text")
    record_compatibility_evidence(
        kind="unsupported_action",
        charger=selected,
        protocol="ocpp1.6",
        unique_id="vendor-9",
        action="VendorMagic",
        details={"payload": {"mode": "field"}},
    )
    stdout = StringIO()

    call_command(
        "ocpp_compatibility",
        charger=selected.identity,
        stdout=stdout,
    )

    output = stdout.getvalue()
    assert "compatibility_events: 1" in output
    assert "unsupported_action=1" in output
    assert "charger=compat-text" in output
    assert "protocol=ocpp1.6" in output
    assert "action=VendorMagic" in output
    assert "id=vendor-9" in output


def test_ocpp_compatibility_rejects_non_positive_limit() -> None:
    with pytest.raises(CommandError, match="limit must be positive"):
        call_command("ocpp_compatibility", limit=0)
