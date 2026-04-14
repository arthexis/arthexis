"""Tests for charger console header reference synchronization."""

from __future__ import annotations

import pytest

from apps.links.models import Reference
from apps.ocpp.consumers.csms.consumer import CSMSConsumer
from apps.ocpp.models import Charger


pytestmark = pytest.mark.django_db


def test_console_reference_updates_existing_header_reference(monkeypatch) -> None:
    charger = Charger.objects.create(charger_id="CP-SYNC-1")
    existing = Reference.objects.create(
        alt_text="CP-SYNC-1 Console",
        value="http://10.0.0.5:8080",
        method="link",
        show_in_header=True,
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = charger
    consumer.charger_id = charger.charger_id
    consumer.client_ip = "10.0.0.7"
    monkeypatch.setattr(
        "apps.ocpp.consumers.csms.consumer.scan_open_ports",
        lambda _host: [443],
    )

    consumer._ensure_console_reference()

    existing.refresh_from_db()
    assert existing.value == "https://10.0.0.7:443"
    assert (
        Reference.objects.filter(alt_text="CP-SYNC-1 Console", show_in_header=True).count()
        == 1
    )
