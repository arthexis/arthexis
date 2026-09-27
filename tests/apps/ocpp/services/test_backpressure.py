from unittest.mock import Mock, patch

import pytest

from apps.ocpp.models import NotificationRecord
from apps.ocpp.services.intake import process_data_transfer
from tests.apps.ocpp.builders import charger


pytestmark = pytest.mark.django_db


def test_data_transfer_ack_survives_secondary_publication_failure(monkeypatch) -> None:
    selected = charger("backpressure-charger")
    callbacks = []

    monkeypatch.setattr(
        "apps.ocpp.services.intake.transaction.on_commit",
        callbacks.append,
    )

    response = process_data_transfer(
        charger=selected,
        payload={"vendorId": "vendor", "data": "historical"},
    )

    assert response == {"status": "Accepted"}
    retained = NotificationRecord.objects.get(
        charger=selected,
        action="DataTransfer",
    )
    assert retained.payload["data"] == "historical"
    assert len(callbacks) == 1

    publish = Mock(side_effect=ConnectionError("broker unavailable"))
    with patch("apps.ocpp.services.intake.publish_safely", publish):
        callbacks[0]()

    publish.assert_called_once()
    retained.refresh_from_db()
    assert retained.payload["data"] == "historical"


def test_sustained_data_transfer_intake_is_not_request_rate_limited(monkeypatch) -> None:
    selected = charger("backlog-drain")
    callbacks = []
    monkeypatch.setattr(
        "apps.ocpp.services.intake.transaction.on_commit",
        callbacks.append,
    )

    for index in range(500):
        response = process_data_transfer(
            charger=selected,
            payload={"vendorId": "vendor", "data": str(index)},
        )
        assert response == {"status": "Accepted"}

    assert NotificationRecord.objects.filter(charger=selected).count() == 500
    assert len(callbacks) == 500
