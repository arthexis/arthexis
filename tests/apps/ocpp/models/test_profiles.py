import pytest

from apps.ocpp.domain.profiles import record_profile
from tests.apps.ocpp.builders import charger

pytestmark = pytest.mark.django_db


def test_record_profile_persists_active_profile() -> None:
    profile = record_profile(
        charger=charger("charger-1"),
        remote_id="profile-1",
        purpose="TxDefaultProfile",
        kind="Absolute",
        payload={"chargingSchedule": {}},
    )

    assert profile.active
