from django.test import TestCase

from apps.ocpp.domain.profiles import record_profile
from tests.apps.ocpp.builders import charger


class ChargingProfileTests(TestCase):
    def test_record_profile_persists_active_profile(self) -> None:
        profile = record_profile(
            charger=charger("charger-1"),
            remote_id="profile-1",
            purpose="TxDefaultProfile",
            kind="Absolute",
            payload={"chargingSchedule": {}},
        )

        self.assertTrue(profile.active)
