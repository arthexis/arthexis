import json
from io import StringIO

import pytest
from django.core.management import call_command

from apps.ocpp.services.display_status import DISPLAY_STATUS_FIELDS
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db(transaction=True)


def test_ocpp_status_emits_json():
    selected = charger("display-json")
    stdout = StringIO()

    call_command("ocpp_status", charger=selected.identity, json_output=True, stdout=stdout)

    payload = json.loads(stdout.getvalue())
    assert tuple(payload) == tuple(sorted(DISPLAY_STATUS_FIELDS))
    assert payload["charger"] == selected.identity
    assert payload["condition"] == "unknown"
    assert payload["connection_live"] is False


def test_ocpp_status_text_follows_renderer_field_order():
    selected = charger("display-contract")
    stdout = StringIO()

    call_command("ocpp_status", charger=selected.identity, stdout=stdout)

    lines = [line for line in stdout.getvalue().splitlines() if line]
    assert tuple(line.split(":", 1)[0] for line in lines) == DISPLAY_STATUS_FIELDS
    assert lines[0] == "charger: display-contract"
    assert "condition: unknown" in lines
    assert "connection_live: False" in lines
