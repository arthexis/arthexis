from datetime import datetime, timezone

import pytest

from apps.ocpp.domain.reservations import record_reservation
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_record_reservation_persists_pending_reservation() -> None:
    reservation = record_reservation(
        charger=charger("charger-1"),
        remote_id="reservation-1",
        id_tag="card-1",
        expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert reservation.status == "pending"
