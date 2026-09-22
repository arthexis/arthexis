from datetime import datetime, timezone

from django.test import TestCase

from apps.ocpp.domain.reservations import record_reservation
from tests.apps.ocpp.builders import charger


class ReservationTests(TestCase):
    def test_record_reservation_persists_pending_reservation(self) -> None:
        reservation = record_reservation(
            charger=charger("charger-1"),
            remote_id="reservation-1",
            id_tag="card-1",
            expires_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(reservation.status, "pending")
